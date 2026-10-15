"""Proceso de carga: versión nueva → validación → publicación atómica o cuarentena.

Reglas:
- Nunca se borra ni se modifica una versión publicada.
- Si cualquier validación bloqueante falla, la versión queda en cuarentena y la fuente sigue
  apuntando a su versión publicada anterior.
- Si ocurre un error durante la carga, se revierte la transacción y se registra una versión en
  cuarentena con el motivo, sin tocar la versión publicada.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

import psycopg

from . import validacion as v
from .arcgis import ClienteArcGIS
from .descargas import contar_lote_estrato_csv_zip, leer_geojson, leer_procedencia, sha256_archivo
from .registro import Fuente

LOTE_INSERCION = 1000


# --- protección de datos reales -----------------------------------------------------------------
def version_real_publicada(conn, fuente_id: str) -> int | None:
    """Versión publicada NO sintética de la fuente, si existe."""
    fila = conn.execute(
        """SELECT v.version_id FROM geo.fuente f JOIN geo.version v ON v.version_id = f.version_publicada
           WHERE f.fuente_id = %s AND NOT v.sintetico""", (fuente_id,)).fetchone()
    return fila["version_id"] if fila else None


def _rechazo_sintetico(conn, fuente_id: str, sintetico: bool) -> dict[str, Any] | None:
    """Los datos sintéticos nunca reemplazan una versión real publicada."""
    if not sintetico:
        return None
    real = version_real_publicada(conn, fuente_id)
    if real is None:
        return None
    return {"version_id": None, "estado": "rechazada",
            "motivo": (f"{fuente_id} tiene publicada la versión real {real}; los datos sintéticos no la "
                       "reemplazan. No se creó ninguna versión.")}


def retirar_sinteticos(conn) -> list[dict[str, Any]]:
    """Deja de publicar las versiones sintéticas (estado 'retirada'). No borra filas.

    La fuente queda sin versión publicada (no habilitada) hasta que se cargue una versión real.
    Nunca toca versiones reales.
    """
    filas = conn.execute(
        """SELECT f.fuente_id, v.version_id FROM geo.fuente f
           JOIN geo.version v ON v.version_id = f.version_publicada
           WHERE v.sintetico FOR UPDATE OF f""").fetchall()
    for r in filas:
        conn.execute("UPDATE geo.fuente SET version_publicada = NULL WHERE fuente_id = %s", (r["fuente_id"],))
        conn.execute("UPDATE geo.version SET estado = 'retirada' WHERE version_id = %s AND sintetico",
                     (r["version_id"],))
    conn.commit()
    return [{"fuente": r["fuente_id"], "version_retirada": r["version_id"]} for r in filas]


# --- versiones ---------------------------------------------------------------------------------
def _nueva_version(conn: psycopg.Connection, fuente_id: str, sintetico: bool) -> int:
    return conn.execute(
        "INSERT INTO geo.version (fuente_id, estado, sintetico) VALUES (%s, 'en_proceso', %s) RETURNING version_id",
        (fuente_id, sintetico)).fetchone()["version_id"]


def _cerrar(conn, version_id: int, fuente_id: str, resultados: list[v.Resultado], esperados: int | None,
            recibidos: int, hash_contenido: str | None, bitacora: list[dict[str, Any]]) -> dict[str, Any]:
    bloqueos = v.hay_bloqueos(resultados)
    comunes = (esperados, recibidos, hash_contenido, json.dumps(bitacora, ensure_ascii=False, default=str),
               json.dumps([r.dict() for r in resultados], ensure_ascii=False))
    if bloqueos:
        motivo = "; ".join(f"{r.regla}: {r.detalle}" for r in bloqueos)
        conn.execute(
            """UPDATE geo.version SET estado='cuarentena', fin=now(), registros_esperados=%s,
               registros_recibidos=%s, hash_contenido=%s, bitacora=%s, validaciones=%s,
               motivo_cuarentena=%s WHERE version_id=%s""", (*comunes, motivo, version_id))
        conn.commit()
        return {"version_id": version_id, "estado": "cuarentena", "motivo": motivo,
                "validaciones": [r.dict() for r in resultados]}

    anterior = conn.execute("SELECT version_publicada FROM geo.fuente WHERE fuente_id=%s FOR UPDATE",
                            (fuente_id,)).fetchone()["version_publicada"]
    conn.execute(
        """UPDATE geo.version SET estado='publicada', fin=now(), registros_esperados=%s,
           registros_recibidos=%s, hash_contenido=%s, bitacora=%s, validaciones=%s WHERE version_id=%s""",
        (*comunes, version_id))
    if anterior is not None:
        conn.execute("UPDATE geo.version SET estado='reemplazada' WHERE version_id=%s", (anterior,))
    conn.execute("UPDATE geo.fuente SET version_publicada=%s WHERE fuente_id=%s", (version_id, fuente_id))
    conn.commit()
    return {"version_id": version_id, "estado": "publicada", "reemplaza": anterior,
            "validaciones": [r.dict() for r in resultados]}


def _cuarentena_por_error(conn, fuente_id: str, error: Exception, sintetico: bool,
                          bitacora: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    conn.rollback()
    motivo = f"Error durante la carga: {type(error).__name__}: {error}"
    version_id = conn.execute(
        """INSERT INTO geo.version (fuente_id, estado, sintetico, fin, motivo_cuarentena, bitacora)
           VALUES (%s, 'cuarentena', %s, now(), %s, %s) RETURNING version_id""",
        (fuente_id, sintetico, motivo, json.dumps(bitacora or [], ensure_ascii=False, default=str))
    ).fetchone()["version_id"]
    conn.commit()
    return {"version_id": version_id, "estado": "cuarentena", "motivo": motivo}


# --- inserción ---------------------------------------------------------------------------------
def _insertar_objetos(conn, version_id: int, fuente_id: str, filas: list[tuple], srid_origen: int) -> None:
    sql_geom = ("ST_Multi(ST_CollectionExtract(ST_Transform(ST_SetSRID(ST_GeomFromGeoJSON(%s), %s), 4326), 3))")
    with conn.cursor() as cur:
        for i in range(0, len(filas), LOTE_INSERCION):
            bloque = filas[i:i + LOTE_INSERCION]
            cur.executemany(
                f"""INSERT INTO geo.objeto (version_id, fuente_id, llave, nombre, padre, atributos, geom)
                    VALUES (%s, %s, %s, %s, %s, %s, CASE WHEN %s::text IS NULL THEN NULL ELSE {sql_geom} END)""",
                [(version_id, fuente_id, llave, nombre, padre, json.dumps(attrs, ensure_ascii=False),
                  geom, geom, srid_origen) for (llave, nombre, padre, attrs, geom) in bloque])


def _validar_geometrias_bd(conn, version_id: int) -> v.Resultado:
    invalidas_antes = conn.execute(
        "SELECT count(*) AS n FROM geo.objeto WHERE version_id=%s AND geom IS NOT NULL AND NOT ST_IsValid(geom)",
        (version_id,)).fetchone()["n"]
    if invalidas_antes:
        conn.execute(
            """UPDATE geo.objeto SET geom = ST_Multi(ST_CollectionExtract(ST_MakeValid(geom), 3))
               WHERE version_id=%s AND geom IS NOT NULL AND NOT ST_IsValid(geom)""", (version_id,))
    fila = conn.execute(
        """SELECT count(*) FILTER (WHERE geom IS NOT NULL AND NOT ST_IsValid(geom)) AS invalidas,
                  count(*) FILTER (WHERE geom IS NOT NULL AND ST_IsEmpty(geom)) AS vacias
           FROM geo.objeto WHERE version_id=%s""", (version_id,)).fetchone()
    ok = fila["invalidas"] == 0 and fila["vacias"] == 0
    return v.Resultado("geometria_valida", ok, True,
                       f"{invalidas_antes} geometrías corregidas con ST_MakeValid; "
                       f"{fila['invalidas']} siguen inválidas; {fila['vacias']} vacías.")


def _validacion_procedencia(procedencia: dict[str, Any], sintetico: bool) -> v.Resultado:
    if sintetico:
        return v.Resultado("procedencia", False, False, "Datos sintéticos: no proceden de una fuente oficial.")
    ok = procedencia.get("tipo") == "descarga_verificada"
    return v.Resultado("procedencia", ok, False,
                       f"Descargado de {procedencia.get('url')} el {procedencia.get('descargado_en')}." if ok else
                       f"Procedencia no verificada: {procedencia.get('motivo')} La versión puede revisarse "
                       "internamente, pero no cuenta como dato oficial en la verificación de extremo a extremo.")


def _hash_features(features: list[dict[str, Any]]) -> str:
    h = hashlib.sha256()
    for f in features:
        h.update(json.dumps(f, sort_keys=True, default=str).encode())
    return h.hexdigest()


def _filas(features, fuente: Fuente, extras: list[str]) -> list[tuple]:
    c = fuente.campos
    filas = []
    for f in features:
        p = f["properties"]
        llave = p.get(c["llave"])
        llave = None if llave is None else str(llave).strip()
        nombre = p.get(c["nombre"]) if "nombre" in c else None
        padre_campo = c.get("padre") or c.get("manzana")
        padre = p.get(padre_campo) if padre_campo else None
        padre = None if padre is None else str(padre).strip()
        attrs = {k: p.get(c[k]) for k in extras if k in c}
        if "estrato" in attrs and attrs["estrato"] is not None:
            try:
                attrs["estrato"] = int(attrs["estrato"])
            except (TypeError, ValueError):
                pass
        geom = json.dumps(f["geometry"]) if f.get("geometry") else None
        filas.append((llave, nombre, padre, attrs, geom))
    return filas


# --- cargas por tipo de fuente -----------------------------------------------------------------
def cargar_features(conn, fuente: Fuente, features: list[dict[str, Any]], campos_disponibles: list[str],
                    dominio: dict[int, str], esperados: int | None, bitacora: list[dict[str, Any]],
                    hash_contenido: str | None = None, con_geometria: bool = True,
                    srid_origen: int = 4326, sintetico: bool = False,
                    extra: list[v.Resultado] | None = None) -> dict[str, Any]:
    if rechazo := _rechazo_sintetico(conn, fuente.id, sintetico):
        return rechazo
    version_id = _nueva_version(conn, fuente.id, sintetico)
    try:
        resultados = [v.campos_presentes(fuente.campos.values(), campos_disponibles)]
        if not resultados[0].ok:
            resultados += [v.licencia(fuente.licencia_verificada, fuente.licencia), v.fecha_dato(fuente.fecha_dato)]
            return _cerrar(conn, version_id, fuente.id, resultados, esperados, len(features),
                           hash_contenido, bitacora)
        filas = _filas(features, fuente, ["estrato"])
        resultados += [
            v.conteo(esperados, len(features)),
            v.no_vacio(len(features)),
            v.llaves_unicas(f[0] for f in filas),
        ]
        if "estrato" in fuente.campos:
            resultados.append(v.dominio_estrato((f[3].get("estrato") for f in filas), dominio))
        if con_geometria:
            geoms = [f.get("geometry") for f in features]
            resultados += [v.geometria_presente_y_tipo(geoms), v.dentro_de_bogota(geoms)]
        resultados += [v.licencia(fuente.licencia_verificada, fuente.licencia), v.fecha_dato(fuente.fecha_dato)]
        resultados += extra or []

        if not v.hay_bloqueos(resultados):
            _insertar_objetos(conn, version_id, fuente.id, filas, srid_origen)
            if con_geometria:
                resultados.append(_validar_geometrias_bd(conn, version_id))
        return _cerrar(conn, version_id, fuente.id, resultados, esperados, len(features),
                       hash_contenido or _hash_features(features), bitacora)
    except Exception as e:  # noqa: BLE001 — cualquier fallo deja la versión anterior intacta
        return _cuarentena_por_error(conn, fuente.id, e, sintetico, bitacora)


def cargar_desde_arcgis(conn, fuente: Fuente, dominio: dict[int, str],
                        cliente: ClienteArcGIS | None = None) -> dict[str, Any]:
    cliente = cliente or ClienteArcGIS()
    con_geometria = fuente.tipo == "arcgis_rest"
    try:
        r = cliente.descargar(fuente.url, list(fuente.campos.values()), con_geometria=con_geometria)
    except Exception as e:  # noqa: BLE001
        return _cuarentena_por_error(conn, fuente.id, e, False, cliente.bitacora)
    return cargar_features(conn, fuente, r.features, r.campos_servicio, dominio, r.esperados, r.bitacora,
                           con_geometria=con_geometria)


def cargar_desde_geojson(conn, fuente: Fuente, ruta, dominio: dict[int, str], srid_origen: int | None = None,
                         sintetico: bool = False) -> dict[str, Any]:
    """srid_origen=None usa el SRID declarado en el archivo (4326 si no declara ninguno)."""
    try:
        features, campos, srid_archivo = leer_geojson(ruta)
    except Exception as e:  # noqa: BLE001
        return _cuarentena_por_error(conn, fuente.id, e, sintetico)
    sha = sha256_archivo(ruta)
    procedencia = leer_procedencia(ruta, sha, fuente.id, fuente.url)
    bitacora = [{"archivo": str(ruta), "sha256": sha, "procedencia": procedencia}]
    return cargar_features(conn, fuente, features, campos, dominio, None, bitacora,
                           hash_contenido=sha, srid_origen=srid_origen or srid_archivo,
                           sintetico=sintetico, extra=[_validacion_procedencia(procedencia, sintetico)])


def cargar_desde_csv_zip(conn, fuente: Fuente, ruta, dominio: dict[int, str],
                         sintetico: bool = False) -> dict[str, Any]:
    if rechazo := _rechazo_sintetico(conn, fuente.id, sintetico):
        return rechazo
    version_id = _nueva_version(conn, fuente.id, sintetico)
    try:
        conteos, columnas, filas = contar_lote_estrato_csv_zip(ruta, fuente.campos["lote"],
                                                               fuente.campos["estrato"])
        sha = sha256_archivo(ruta)
        procedencia = leer_procedencia(ruta, sha, fuente.id, fuente.url)
        bitacora = [{"archivo": str(ruta), "sha256": sha, "filas": filas, "procedencia": procedencia}]
        resultados = [v.campos_presentes(fuente.campos.values(), columnas)]
        if resultados[0].ok:
            resultados += [v.no_vacio(filas),
                           v.dominio_estrato((e for (_, e) in conteos.keys()), dominio)]
            lotes_vacios = sum(n for (lote, _), n in conteos.items() if not lote)
            resultados.append(v.Resultado("lote_presente", lotes_vacios == 0, True,
                                          f"{lotes_vacios} predios sin código de lote."))
        resultados += [v.licencia(fuente.licencia_verificada, fuente.licencia), v.fecha_dato(fuente.fecha_dato),
                       _validacion_procedencia(procedencia, sintetico)]
        if not v.hay_bloqueos(resultados):
            with conn.cursor() as cur:
                cur.executemany(
                    "INSERT INTO geo.conteo_lote_estrato (version_id, lote, estrato, n_predios) VALUES (%s,%s,%s,%s)",
                    [(version_id, lote, int(e), n) for (lote, e), n in conteos.items()])
        return _cerrar(conn, version_id, fuente.id, resultados, None, filas, sha, bitacora)
    except Exception as e:  # noqa: BLE001
        return _cuarentena_por_error(conn, fuente.id, e, sintetico)
