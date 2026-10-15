"""Consultas para la API: estado de fuentes, capas, búsqueda y fichas territoriales.

Principios:
- Solo se exponen fuentes habilitadas (licencia verificada, sin pendientes bloqueantes y con una
  versión publicada). Las demás aparecen con estado "no_validada" y sus motivos.
- Las distribuciones de estrato se presentan como conteos y porcentajes; nunca como promedio.
- La estratificación 2025 (manzanas), la distribución de predios 2026 y la capa histórica 2019
  se presentan en bloques separados y no se combinan.
"""

from __future__ import annotations

import json
from datetime import date
from typing import Any

from .registro import advertencias_fecha, cargar_registro, motivos_no_habilitada

NIVELES = {
    "localidad": "mr_localidad",
    "upl": "mr_upl",
    "sector": "mr_sector",
    "manzana": "mr_manzana",
}

CAPAS_ESTRATO = {
    "estrato_2025": "sdp_estrato_manzana_2025",
    "estrato_2019": "sdp_estrato_manzana_2019_rest",
}

ADVERTENCIA_ESTRATO = ("El estrato clasifica inmuebles residenciales para el cobro diferencial de "
                       "servicios públicos. No mide ingresos, preferencias ni características de las personas.")


def _dominio() -> dict[int, str]:
    return cargar_registro()["dominio_estrato"]


# --- fuentes -----------------------------------------------------------------------------------
def estado_fuentes(conn, hoy: date | None = None) -> list[dict[str, Any]]:
    filas = conn.execute(
        """SELECT f.*, vp.fin AS publicada_en, vp.registros_recibidos AS publicada_registros,
                  vp.sintetico AS publicada_sintetica,
                  ult.version_id AS ultima_version, ult.estado AS ultima_estado,
                  ult.motivo_cuarentena AS ultima_motivo, ult.inicio AS ultima_inicio
           FROM geo.fuente f
           LEFT JOIN geo.version vp ON vp.version_id = f.version_publicada
           LEFT JOIN LATERAL (SELECT * FROM geo.version v WHERE v.fuente_id = f.fuente_id
                              ORDER BY v.version_id DESC LIMIT 1) ult ON true
           ORDER BY f.fuente_id""").fetchall()
    salida = []
    for f in filas:
        motivos = motivos_no_habilitada(f["licencia_verificada"], f["licencia"], f["pendientes"],
                                        f["version_publicada"])
        salida.append({
            "id": f["fuente_id"], "nombre": f["nombre"], "nivel": f["nivel"], "entidad": f["entidad"],
            "url": f["url"], "ficha_url": f["ficha_url"], "licencia": f["licencia"],
            "licencia_verificada": f["licencia_verificada"],
            "fecha_dato": f["fecha_dato"].isoformat() if f["fecha_dato"] else None,
            "fecha_dato_origen": f["fecha_dato_origen"],
            "advertencias": list(f["advertencias"]) + advertencias_fecha(f["fecha_dato"], hoy),
            "pendientes": f["pendientes"],
            "habilitada": not motivos, "motivos": motivos,
            "version_publicada": f["version_publicada"],
            "publicada_en": f["publicada_en"].isoformat() if f["publicada_en"] else None,
            "publicada_registros": f["publicada_registros"],
            "sintetico": bool(f["publicada_sintetica"]),
            "ultima_version": {"id": f["ultima_version"], "estado": f["ultima_estado"],
                               "motivo": f["ultima_motivo"]} if f["ultima_version"] else None,
        })
    return salida


def _fuentes_por_id(conn) -> dict[str, dict[str, Any]]:
    return {f["id"]: f for f in estado_fuentes(conn)}


def _ref_fuente(f: dict[str, Any]) -> dict[str, Any]:
    claves = ("id", "nombre", "entidad", "url", "ficha_url", "licencia", "fecha_dato",
              "fecha_dato_origen", "version_publicada", "publicada_en", "sintetico")
    return {k: f.get(k) for k in claves}


# --- capas -------------------------------------------------------------------------------------
class NoHabilitada(Exception):
    def __init__(self, fuente: dict[str, Any]):
        super().__init__(f"Fuente no habilitada: {fuente['id']}")
        self.fuente = fuente


def _bbox_sql(bbox: tuple[float, float, float, float] | None) -> tuple[str, list[Any]]:
    if not bbox:
        return "", []
    return " AND geom && ST_MakeEnvelope(%s, %s, %s, %s, 4326)", list(bbox)


def capa_geojson(conn, capa: str, bbox: tuple[float, float, float, float] | None,
                 limite: int = 6000) -> dict[str, Any]:
    fuentes = _fuentes_por_id(conn)
    fuente_id = NIVELES.get(capa) or CAPAS_ESTRATO.get(capa)
    if not fuente_id:
        raise KeyError(capa)
    fuente = fuentes.get(fuente_id)
    if not fuente or not fuente["habilitada"]:
        raise NoHabilitada(fuente or {"id": fuente_id, "motivos": ["Fuente no registrada."]})
    filtro, params = _bbox_sql(bbox)
    dominio = _dominio()
    filas = conn.execute(
        f"""SELECT llave, nombre, padre, atributos, ST_AsGeoJSON(geom, 6) AS g
            FROM geo.v_objeto_publicado WHERE fuente_id = %s AND geom IS NOT NULL {filtro}
            ORDER BY llave LIMIT %s""", [fuente_id, *params, limite + 1]).fetchall()
    truncada = len(filas) > limite
    features = []
    for f in filas[:limite]:
        props = {"codigo": f["llave"], "nombre": f["nombre"], "padre": f["padre"], "capa": capa}
        if "estrato" in f["atributos"]:
            e = f["atributos"]["estrato"]
            props["estrato"] = e
            props["estrato_etiqueta"] = dominio.get(e, f"Valor {e}")
        features.append({"type": "Feature", "geometry": json.loads(f["g"]), "properties": props})
    return {"type": "FeatureCollection", "features": features, "truncada": truncada,
            "fuente": _ref_fuente(fuente)}


# --- búsqueda ----------------------------------------------------------------------------------
def buscar(conn, texto: str, limite: int = 20) -> list[dict[str, Any]]:
    fuentes = _fuentes_por_id(conn)
    habilitadas = {nivel: fid for nivel, fid in NIVELES.items()
                   if fuentes.get(fid, {}).get("habilitada")}
    if not habilitadas or not texto.strip():
        return []
    nivel_por_fuente = {fid: nivel for nivel, fid in habilitadas.items()}
    patron = f"%{texto.strip()}%"
    filas = conn.execute(
        """SELECT fuente_id, llave, nombre FROM geo.v_objeto_publicado
           WHERE fuente_id = ANY(%s) AND (nombre ILIKE %s OR llave = %s)
           ORDER BY (llave = %s) DESC, fuente_id, nombre LIMIT %s""",
        (list(habilitadas.values()), patron, texto.strip(), texto.strip(), limite)).fetchall()
    return [{"nivel": nivel_por_fuente[f["fuente_id"]], "codigo": f["llave"], "nombre": f["nombre"]}
            for f in filas]


# --- fichas ------------------------------------------------------------------------------------
def _manzanas_de(nivel: str, codigo: str) -> tuple[str, list[Any], str]:
    """SQL que devuelve los códigos de manzana de la unidad, y el método usado."""
    if nivel == "manzana":
        return "SELECT %s::text AS manzana", [codigo], "La propia manzana."
    if nivel == "sector":
        return ("""SELECT llave AS manzana FROM geo.v_objeto_publicado
                   WHERE fuente_id='mr_manzana' AND padre = %s""", [codigo],
                "Manzanas cuyo código de sector (SECCODIGO) coincide con el sector, según el Mapa de Referencia.")
    fuente = NIVELES[nivel]
    return ("""SELECT m.llave AS manzana
               FROM geo.v_objeto_publicado u
               JOIN geo.v_objeto_publicado m
                 ON m.fuente_id='mr_manzana' AND ST_Contains(u.geom, ST_PointOnSurface(m.geom))
               WHERE u.fuente_id = %s AND u.llave = %s""", [fuente, codigo],
            "Manzanas cuyo punto interior (ST_PointOnSurface) cae dentro de la unidad.")


def _distribucion(conteos: dict[int, int], dominio: dict[int, str]) -> tuple[list[dict[str, Any]], int]:
    total = sum(conteos.values())
    filas = []
    for e in sorted(conteos):
        n = conteos[e]
        filas.append({"estrato": e, "etiqueta": dominio.get(e, f"Valor {e}"), "n": n,
                      "porcentaje": round(100 * n / total, 1) if total else None})
    return filas, total


def _bloque_no_validado(id_: str, titulo: str, fuentes: list[dict[str, Any]]) -> dict[str, Any]:
    motivos = []
    for f in fuentes:
        for m in f.get("motivos") or ["Fuente no registrada."]:
            motivos.append(f"{f.get('nombre', f.get('id'))}: {m}")
    return {"id": id_, "titulo": titulo, "estado": "no_validada", "motivos": motivos,
            "fuentes": [_ref_fuente(f) for f in fuentes if f.get("nombre")]}


def ficha(conn, nivel: str, codigo: str) -> dict[str, Any] | None:
    if nivel not in NIVELES:
        raise KeyError(nivel)
    fuentes = _fuentes_por_id(conn)
    dominio = _dominio()
    fuente_unidad = fuentes.get(NIVELES[nivel])
    if not fuente_unidad or not fuente_unidad["habilitada"]:
        raise NoHabilitada(fuente_unidad or {"id": NIVELES[nivel], "motivos": ["Fuente no registrada."]})

    unidad = conn.execute(
        """SELECT llave, nombre, padre, atributos,
                  round((ST_Area(geom::geography) / 10000)::numeric, 2) AS area_ha,
                  ST_AsGeoJSON(ST_Envelope(geom), 6) AS caja
           FROM geo.v_objeto_publicado WHERE fuente_id=%s AND llave=%s""",
        (NIVELES[nivel], codigo)).fetchone()
    if not unidad:
        return None

    sql_manzanas, params_manzanas, metodo = _manzanas_de(nivel, codigo)
    n_manzanas = conn.execute(f"SELECT count(*) AS n FROM ({sql_manzanas}) s", params_manzanas).fetchone()["n"]
    bloques = []

    # 1. Estrato asignado por manzana (2025)
    f25 = fuentes.get("sdp_estrato_manzana_2025")
    titulo25 = "Estrato asignado por manzana (2025)"
    if f25 and f25["habilitada"]:
        filas = conn.execute(
            f"""WITH m AS ({sql_manzanas})
                SELECT (e.atributos->>'estrato')::int AS estrato, count(*) AS n
                FROM m JOIN geo.v_objeto_publicado e
                  ON e.fuente_id='sdp_estrato_manzana_2025' AND e.llave = m.manzana
                GROUP BY 1""", params_manzanas).fetchall()
        distribucion, total = _distribucion({r["estrato"]: r["n"] for r in filas}, dominio)
        bloques.append({
            "id": "estrato_2025", "titulo": titulo25, "estado": "disponible" if total else "sin_datos",
            "unidad_medida": "manzanas",
            "distribucion": distribucion, "total": total,
            "manzanas_en_unidad": n_manzanas, "manzanas_sin_coincidencia": n_manzanas - total,
            "metodo": f"{metodo} Unión por código de manzana idéntico, sin transformar códigos.",
            "fuente": _ref_fuente(f25), "advertencias": f25["advertencias"],
        })
    else:
        bloques.append(_bloque_no_validado("estrato_2025", titulo25, [f25 or {"id": "sdp_estrato_manzana_2025"}]))

    # 2. Predios residenciales por estrato (2026)
    f26 = fuentes.get("sdp_estrato_predio_2026")
    flote = fuentes.get("mr_lote_manzana")
    titulo26 = "Predios residenciales por estrato (2026)"
    if f26 and f26["habilitada"] and flote and flote["habilitada"]:
        filas = conn.execute(
            f"""WITH m AS ({sql_manzanas})
                SELECT p.estrato, sum(p.n_predios)::bigint AS n
                FROM m JOIN geo.v_predios_manzana p ON p.manzana = m.manzana
                GROUP BY 1""", params_manzanas).fetchall()
        distribucion, total = _distribucion({r["estrato"]: int(r["n"]) for r in filas}, dominio)
        bloques.append({
            "id": "predios_2026", "titulo": titulo26, "estado": "disponible" if total else "sin_datos",
            "unidad_medida": "predios",
            "distribucion": distribucion, "total": total,
            "metodo": (f"{metodo} Predios unidos a su lote por el código de lote y el lote a su manzana por "
                       "MANZCODIGO (relación oficial 'Pertenece a la manzana' de la capa Lote)."),
            "fuente": _ref_fuente(f26), "fuente_relacion": _ref_fuente(flote),
            "advertencias": f26["advertencias"],
        })
    else:
        bloques.append(_bloque_no_validado(
            "predios_2026", titulo26,
            [x for x in (f26 or {"id": "sdp_estrato_predio_2026"}, flote or {"id": "mr_lote_manzana"})
             if not x.get("habilitada")]))

    # 3. Capa histórica (2019): solo si estuviera habilitada; nunca se mezcla con 2025
    f19 = fuentes.get("sdp_estrato_manzana_2019_rest")
    titulo19 = "Estratificación histórica por manzana (dato de 2019)"
    if f19 and f19["habilitada"]:
        filas = conn.execute(
            f"""WITH m AS ({sql_manzanas})
                SELECT (e.atributos->>'estrato')::int AS estrato, count(*) AS n
                FROM m JOIN geo.v_objeto_publicado e
                  ON e.fuente_id='sdp_estrato_manzana_2019_rest' AND e.llave = m.manzana
                GROUP BY 1""", params_manzanas).fetchall()
        distribucion, total = _distribucion({r["estrato"]: r["n"] for r in filas}, dominio)
        bloques.append({"id": "estrato_2019", "titulo": titulo19, "estado": "disponible" if total else "sin_datos",
                        "unidad_medida": "manzanas", "distribucion": distribucion, "total": total,
                        "metodo": metodo, "fuente": _ref_fuente(f19), "advertencias": f19["advertencias"]})
    else:
        bloques.append(_bloque_no_validado("estrato_2019", titulo19, [f19 or {"id": "sdp_estrato_manzana_2019_rest"}]))

    usadas = [fuente_unidad] + [b["fuente"] for b in bloques if b.get("fuente")]
    return {
        "nivel": nivel, "codigo": unidad["llave"], "nombre": unidad["nombre"], "padre": unidad["padre"],
        "area_ha": float(unidad["area_ha"]) if unidad["area_ha"] is not None else None,
        "caja": json.loads(unidad["caja"]) if unidad["caja"] else None,
        "manzanas_en_unidad": n_manzanas,
        "fuente_unidad": _ref_fuente(fuente_unidad),
        "advertencias_generales": [ADVERTENCIA_ESTRATO] + fuente_unidad["advertencias"],
        "bloques": bloques,
        "contiene_datos_sinteticos": any(f.get("sintetico") for f in usadas),
    }


# --- consistencia entre fuentes ----------------------------------------------------------------
def consistencia(conn) -> list[dict[str, Any]]:
    """Informes de consistencia. Se calculan solo con versiones publicadas; no fusionan fuentes."""
    informes = []

    def publicado(fid):
        return conn.execute("SELECT version_publicada FROM geo.fuente WHERE fuente_id=%s",
                            (fid,)).fetchone() or {}

    def hay(fid):
        return bool(publicado(fid).get("version_publicada"))

    if hay("mr_manzana") and hay("sdp_estrato_manzana_2025"):
        r = conn.execute(
            """SELECT count(*) AS total,
                      count(*) FILTER (WHERE m.llave IS NOT NULL) AS con_coincidencia
               FROM geo.v_objeto_publicado e
               LEFT JOIN geo.v_objeto_publicado m ON m.fuente_id='mr_manzana' AND m.llave=e.llave
               WHERE e.fuente_id='sdp_estrato_manzana_2025'""").fetchone()
        informes.append({"informe": "Códigos de manzana 2025 encontrados en el Mapa de Referencia", **r,
                         "porcentaje": round(100 * r["con_coincidencia"] / r["total"], 2) if r["total"] else None})

    if hay("sdp_estrato_predio_2026") and hay("mr_lote_manzana"):
        r = conn.execute(
            """SELECT count(*) AS lotes, count(*) FILTER (WHERE l.llave IS NOT NULL) AS con_relacion
               FROM (SELECT DISTINCT lote FROM geo.v_conteo_publicado) c
               LEFT JOIN geo.v_objeto_publicado l ON l.fuente_id='mr_lote_manzana' AND l.llave=c.lote""").fetchone()
        informes.append({"informe": "Lotes de la tabla de predios 2026 con relación lote-manzana", **r,
                         "porcentaje": round(100 * r["con_relacion"] / r["lotes"], 2) if r["lotes"] else None})

    if hay("sdp_estrato_manzana_2025") and hay("sdp_estrato_predio_2026") and hay("mr_lote_manzana"):
        r = conn.execute(
            """WITH moda AS (
                 SELECT DISTINCT ON (manzana) manzana, estrato
                 FROM geo.v_predios_manzana ORDER BY manzana, n_predios DESC, estrato)
               SELECT count(*) AS comparables,
                      count(*) FILTER (WHERE (e.atributos->>'estrato')::int = moda.estrato) AS coinciden
               FROM moda JOIN geo.v_objeto_publicado e
                 ON e.fuente_id='sdp_estrato_manzana_2025' AND e.llave = moda.manzana""").fetchone()
        empates = conn.execute(
            """SELECT count(*) AS n FROM (
                 SELECT manzana FROM geo.v_predios_manzana p
                 WHERE n_predios = (SELECT max(n_predios) FROM geo.v_predios_manzana q WHERE q.manzana = p.manzana)
                 GROUP BY manzana HAVING count(*) > 1) t""").fetchone()["n"]
        informes.append({"informe": "Manzanas donde el estrato 2025 coincide con el estrato más frecuente "
                                    "de sus predios 2026 (comparación, no fusión)", **r,
                         "porcentaje": round(100 * r["coinciden"] / r["comparables"], 2) if r["comparables"] else None,
                         "empates_en_estrato_mas_frecuente": empates,
                         "limitaciones": [
                             "Compara la clasificación de la manzana (2025) con la de sus predios residenciales "
                             "(2026): son fechas, unidades y reglas distintas (atípicos, recursos, VIP); "
                             "una diferencia no implica un error en ninguna de las dos fuentes.",
                             "En las manzanas con empate en el estrato más frecuente se toma el estrato menor; "
                             "el número de empates se informa para dimensionar ese efecto.",
                             "Solo entran manzanas con predios relacionados en 2026 y con estrato en 2025."]})
    informes += consistencia_detalle(conn, hay)
    return informes


def _ejemplos(conn, sql: str) -> tuple[int, list[str]]:
    n = conn.execute(f"SELECT count(*) AS n FROM ({sql}) t").fetchone()["n"]
    ej = [r["codigo"] for r in conn.execute(f"SELECT codigo FROM ({sql}) t ORDER BY codigo LIMIT 10")]
    return n, ej


def consistencia_detalle(conn, hay) -> list[dict[str, Any]]:
    """Integridad de códigos y registros sin correspondencia, con ejemplos. Solo describe: no corrige."""
    informes = []
    columnas = {"mr_manzana": "llave", "sdp_estrato_manzana_2025": "llave", "mr_lote_manzana": "padre"}
    integridad = []
    for fid, col in columnas.items():
        if not hay(fid):
            continue
        r = conn.execute(
            f"""SELECT count(*) AS registros,
                       count(*) FILTER (WHERE {col} IS NULL OR {col} = '') AS vacios,
                       count(*) FILTER (WHERE {col} !~ '^[0-9]+$') AS no_numericos,
                       count(*) FILTER (WHERE {col} <> btrim({col})) AS con_espacios
                FROM geo.v_objeto_publicado WHERE fuente_id = %s""", (fid,)).fetchone()
        longitudes = {str(x["l"]): x["n"] for x in conn.execute(
            f"""SELECT length({col}) AS l, count(*) AS n FROM geo.v_objeto_publicado
                WHERE fuente_id = %s GROUP BY 1 ORDER BY 1""", (fid,))}
        duplicados = conn.execute(
            """SELECT count(*) AS n FROM (SELECT llave FROM geo.v_objeto_publicado WHERE fuente_id = %s
                                          GROUP BY llave HAVING count(*) > 1) t""", (fid,)).fetchone()["n"]
        integridad.append({"fuente": fid, "campo": "código de manzana" if col == "llave" else "manzana del lote",
                           **r, "longitudes": longitudes, "llaves_duplicadas": duplicados})
    if integridad:
        informes.append({"informe": "Integridad de los códigos de manzana", "fuentes": integridad,
                         "limitaciones": ["Los códigos se comparan tal como los publica cada fuente; no se "
                                          "rellenan, recortan ni transforman. Longitudes distintas entre fuentes "
                                          "explicarían una correspondencia baja."]})

    if hay("mr_manzana") and hay("sdp_estrato_manzana_2025"):
        n, ej = _ejemplos(conn, """SELECT e.llave AS codigo FROM geo.v_objeto_publicado e
            WHERE e.fuente_id='sdp_estrato_manzana_2025' AND NOT EXISTS (SELECT 1 FROM geo.v_objeto_publicado m
              WHERE m.fuente_id='mr_manzana' AND m.llave = e.llave)""")
        informes.append({"informe": "Códigos de estratificación 2025 sin manzana en el Mapa de Referencia",
                         "registros": n, "ejemplos": ej})
        n, ej = _ejemplos(conn, """SELECT m.llave AS codigo FROM geo.v_objeto_publicado m
            WHERE m.fuente_id='mr_manzana' AND NOT EXISTS (SELECT 1 FROM geo.v_objeto_publicado e
              WHERE e.fuente_id='sdp_estrato_manzana_2025' AND e.llave = m.llave)""")
        informes.append({"informe": "Manzanas del Mapa de Referencia sin estrato asignado en 2025",
                         "registros": n, "ejemplos": ej,
                         "limitaciones": ["Puede tratarse de manzanas no residenciales o sin estratificar; "
                                          "no se interpreta como error sin revisar casos."]})
    if hay("mr_manzana") and hay("mr_lote_manzana"):
        n, ej = _ejemplos(conn, """SELECT DISTINCT l.padre AS codigo FROM geo.v_objeto_publicado l
            WHERE l.fuente_id='mr_lote_manzana' AND NOT EXISTS (SELECT 1 FROM geo.v_objeto_publicado m
              WHERE m.fuente_id='mr_manzana' AND m.llave = l.padre)""")
        informes.append({"informe": "Manzanas citadas por la relación lote-manzana que no existen en la capa Manzana",
                         "registros": n, "ejemplos": ej})
    if hay("sdp_estrato_predio_2026") and hay("mr_lote_manzana"):
        n, ej = _ejemplos(conn, """SELECT DISTINCT c.lote AS codigo FROM geo.v_conteo_publicado c
            WHERE NOT EXISTS (SELECT 1 FROM geo.v_objeto_publicado l
              WHERE l.fuente_id='mr_lote_manzana' AND l.llave = c.lote)""")
        predios = conn.execute("""SELECT coalesce(sum(c.n_predios), 0)::bigint AS n FROM geo.v_conteo_publicado c
            WHERE NOT EXISTS (SELECT 1 FROM geo.v_objeto_publicado l
              WHERE l.fuente_id='mr_lote_manzana' AND l.llave = c.lote)""").fetchone()["n"]
        informes.append({"informe": "Lotes de la tabla de predios 2026 sin relación lote-manzana",
                         "registros": n, "predios_afectados": predios, "ejemplos": ej,
                         "limitaciones": ["Estos predios no se suman a ninguna manzana en las fichas."]})
    return informes


# --- procedencia de lo publicado ---------------------------------------------------------------
ORIGENES_OFICIALES = ("servicio_oficial", "descarga_verificada")


def _origen(f: dict[str, Any], v: dict[str, Any]) -> tuple[str, str]:
    """Clasifica de dónde salió la versión publicada a partir de su bitácora."""
    if v["sintetico"]:
        return "sintetico", "Versión cargada con datos inventados de prueba."
    bitacora = v["bitacora"] or []
    archivo = next((b for b in bitacora if "procedencia" in b), None)
    if archivo:
        p = archivo["procedencia"]
        if p.get("tipo") == "descarga_verificada":
            return "descarga_verificada", (f"Archivo descargado de la URL registrada el {p.get('descargado_en')}; "
                                           f"sha256 {archivo.get('sha256', '')[:12]}… coincide.")
        return "archivo_sin_procedencia", p.get("motivo", "")
    consultas = [b for b in bitacora if str(b.get("url", "")).startswith(f["url"]) and b.get("status") == 200]
    if consultas:
        esperados, recibidos = v["registros_esperados"], v["registros_recibidos"]
        if esperados is not None and esperados == recibidos:
            return "servicio_oficial", (f"{len(consultas)} consultas al servicio registrado; "
                                        f"{recibidos} registros, igual al conteo del servicio.")
        return "servicio_incompleto", f"Conteo del servicio {esperados}, registros recibidos {recibidos}."
    return "desconocida", "La bitácora no permite establecer el origen."


def procedencia(conn) -> list[dict[str, Any]]:
    filas = conn.execute(
        """SELECT f.fuente_id, f.url, v.version_id, v.sintetico, v.registros_esperados, v.registros_recibidos,
                  v.hash_contenido, v.fin, v.bitacora
           FROM geo.fuente f LEFT JOIN geo.version v ON v.version_id = f.version_publicada
           ORDER BY f.fuente_id""").fetchall()
    estado = {f["id"]: f for f in estado_fuentes(conn)}
    salida = []
    for r in filas:
        e = estado[r["fuente_id"]]
        base = {"fuente": r["fuente_id"], "habilitada": e["habilitada"], "motivos": e["motivos"],
                "version_publicada": r["version_id"]}
        if r["version_id"] is None:
            salida.append({**base, "origen": "sin_version", "detalle": "No hay versión publicada.",
                           "oficial": False})
            continue
        origen, detalle = _origen({"url": r["url"]}, r)
        salida.append({**base, "origen": origen, "detalle": detalle, "oficial": origen in ORIGENES_OFICIALES,
                       "sintetico": r["sintetico"], "registros": r["registros_recibidos"],
                       "registros_esperados": r["registros_esperados"], "hash_contenido": r["hash_contenido"],
                       "publicada_en": r["fin"].isoformat() if r["fin"] else None})
    return salida


# --- registro por fuente para la validación -----------------------------------------------------
def informe_fuentes(conn, registro: dict[str, Any]) -> list[dict[str, Any]]:
    """Ficha de validación de cada fuente: metadatos del registro + última carga + versión publicada."""
    proc = {p["fuente"]: p for p in procedencia(conn)}
    est = {f["id"]: f for f in estado_fuentes(conn)}
    salida = []
    for fid, f in registro["fuentes"].items():
        ultima = conn.execute(
            """SELECT version_id, estado, sintetico, inicio, fin, registros_esperados, registros_recibidos,
                      hash_contenido, validaciones, bitacora, motivo_cuarentena
               FROM geo.version WHERE fuente_id = %s ORDER BY version_id DESC LIMIT 1""", (fid,)).fetchone()
        resumen = next((b["resumen"] for b in (ultima or {}).get("bitacora") or [] if "resumen" in b), {})
        e = est.get(fid, {})
        salida.append({
            "fuente": fid, "nombre": f.nombre, "entidad": f.entidad, "tipo": f.tipo, "url": f.url,
            "ficha_url": f.ficha_url, "licencia": f.licencia, "licencia_verificada": f.licencia_verificada,
            "fecha_dato": f.fecha_dato.isoformat() if f.fecha_dato else None,
            "campos_registrados": f.campos, "conteo_verificado_en_registro": f.conteo_verificado,
            "pendientes": f.pendientes,
            "ultima_carga": None if not ultima else {
                "version": ultima["version_id"], "estado": ultima["estado"], "sintetica": ultima["sintetico"],
                "consultada_en": ultima["inicio"].isoformat(timespec="seconds") if ultima["inicio"] else None,
                "registros_esperados": ultima["registros_esperados"],
                "registros_recibidos": ultima["registros_recibidos"],
                "sha256_o_huella": ultima["hash_contenido"],
                "campos_disponibles": resumen.get("campos_disponibles"),
                "srid": resumen.get("srid"), "srid_nota": resumen.get("srid_nota"),
                "validaciones": [{"regla": v["regla"], "ok": v["ok"], "bloqueante": v["bloqueante"],
                                  "detalle": v["detalle"]} for v in ultima["validaciones"] or []],
                "motivo_cuarentena": ultima["motivo_cuarentena"]},
            "estado_final": ("publicada" if e.get("habilitada") else
                             "en cuarentena" if ultima and ultima["estado"] == "cuarentena" else "deshabilitada"),
            "motivos_no_habilitada": e.get("motivos", []),
            "procedencia": {k: proc.get(fid, {}).get(k) for k in ("origen", "oficial", "detalle", "version_publicada")},
        })
    return salida
