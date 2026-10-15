"""Pruebas con PostgreSQL + PostGIS. Usan solo datos sintéticos."""
import copy

import pytest

from territorial import fichas
from territorial.carga import cargar_desde_csv_zip, cargar_features

from territorial import sinteticos as s
from .conftest import zip_csv

pytestmark = pytest.mark.bd


def _cargar(conn, registro, fid, feats, esperados=None, con_geom=True):
    campos = sorted({k for x in feats for k in x["properties"]})
    return cargar_features(conn, registro["fuentes"][fid], feats, campos, registro["dominio_estrato"],
                           len(feats) if esperados is None else esperados, [], con_geometria=con_geom,
                           sintetico=True)


def _publicada(conn, fid):
    return conn.execute("SELECT version_publicada FROM geo.fuente WHERE fuente_id=%s", (fid,)).fetchone()["version_publicada"]


def test_migracion_idempotente(conn):
    from territorial import bd
    bd.migrar(conn)
    bd.migrar(conn)
    assert "3." in bd.postgis_version(conn)


def test_publicacion_y_reemplazo(conn, registro):
    r1 = _cargar(conn, registro, "mr_sector", s.SECTORES)
    r2 = _cargar(conn, registro, "mr_sector", s.SECTORES)
    assert r1["estado"] == r2["estado"] == "publicada" and r2["reemplaza"] == r1["version_id"]
    estados = {r["version_id"]: r["estado"] for r in conn.execute("SELECT version_id, estado FROM geo.version")}
    assert estados == {r1["version_id"]: "reemplazada", r2["version_id"]: "publicada"}
    # La versión anterior se conserva (no se borra)
    assert conn.execute("SELECT count(*) AS n FROM geo.objeto WHERE version_id=%s", (r1["version_id"],)).fetchone()["n"] == 2


@pytest.mark.parametrize("caso", ["perdida", "duplicado", "estrato_invalido", "fuera_de_bogota", "campo_faltante"])
def test_fallo_deja_en_cuarentena_y_conserva_version_anterior(conn, registro, caso):
    buena = _cargar(conn, registro, "sdp_estrato_manzana_2025", s.ESTRATO_2025)
    feats = copy.deepcopy(s.ESTRATO_2025)
    esperados = None
    if caso == "perdida":
        esperados = len(feats) + 5
    elif caso == "duplicado":
        feats.append(copy.deepcopy(feats[0]))
    elif caso == "estrato_invalido":
        feats[0]["properties"]["ESTRATO"] = 8
    elif caso == "fuera_de_bogota":
        feats[0]["geometry"] = s.cuadrado(4880000, 2070000, 100)
    elif caso == "campo_faltante":
        for f in feats:
            f["properties"]["COD_MZ"] = f["properties"].pop("CODIGO_MANZANA")
    r = _cargar(conn, registro, "sdp_estrato_manzana_2025", feats, esperados)
    assert r["estado"] == "cuarentena" and r["motivo"]
    assert _publicada(conn, "sdp_estrato_manzana_2025") == buena["version_id"]
    assert conn.execute("SELECT count(*) AS n FROM geo.objeto WHERE version_id=%s", (r["version_id"],)).fetchone()["n"] == 0


def test_error_inesperado_deja_cuarentena_sin_tocar_la_publicada(conn, registro):
    buena = _cargar(conn, registro, "mr_sector", s.SECTORES)
    feats = copy.deepcopy(s.SECTORES)
    feats[0]["geometry"] = {"type": "Polygon", "coordinates": [[[-74.1, 4.6], [-74.09]]]}  # GeoJSON roto
    r = _cargar(conn, registro, "mr_sector", feats)
    assert r["estado"] == "cuarentena" and "Error durante la carga" in r["motivo"]
    assert _publicada(conn, "mr_sector") == buena["version_id"]
    # La versión a medio cargar se revirtió: no quedan versiones colgadas en proceso
    assert conn.execute("SELECT count(*) AS n FROM geo.version WHERE estado='en_proceso'").fetchone()["n"] == 0


def test_geometria_invalida_se_corrige_y_se_registra(conn, registro):
    feats = copy.deepcopy(s.SECTORES)
    # "Corbatín": polígono que se autointersecta
    feats[0]["geometry"] = {"type": "Polygon", "coordinates": [[[-74.10, 4.60], [-74.09, 4.61], [-74.09, 4.60], [-74.10, 4.61], [-74.10, 4.60]]]}
    r = _cargar(conn, registro, "mr_sector", feats)
    assert r["estado"] == "publicada"
    regla = next(x for x in r["validaciones"] if x["regla"] == "geometria_valida")
    assert regla["ok"] and regla["detalle"].startswith("1 geometrías corregidas")


def test_licencia_pendiente_carga_pero_no_habilita(conn, registro):
    feats = [s.feat({"CODIGO_MANZANA": "M00000001", "ESTRATO": 2}, s.MANZANAS[0]["geometry"])]
    r = _cargar(conn, registro, "sdp_estrato_manzana_2019_rest", feats)
    assert r["estado"] == "publicada"  # se conserva para revisión interna
    f19 = next(f for f in fichas.estado_fuentes(conn) if f["id"] == "sdp_estrato_manzana_2019_rest")
    assert not f19["habilitada"] and any("Licencia" in m for m in f19["motivos"])
    with pytest.raises(fichas.NoHabilitada):
        fichas.capa_geojson(conn, "estrato_2019", (-75, 4, -73, 5))


def test_csv_2026_no_guarda_chip_y_agrega_por_manzana_oficial(datos_cargados):
    conn = datos_cargados
    columnas = [r["column_name"] for r in conn.execute(
        "SELECT column_name FROM information_schema.columns WHERE table_schema='geo' AND table_name='conteo_lote_estrato'")]
    assert not any("chip" in c.lower() for c in columnas)
    filas = {(r["manzana"], r["estrato"]): r["n_predios"] for r in conn.execute("SELECT * FROM geo.v_predios_manzana")}
    assert filas == {("M00000001", 2): 9, ("M00000001", 3): 1, ("M00000003", 3): 7}


def test_ficha_manzana_separa_las_fuentes(datos_cargados):
    f = fichas.ficha(datos_cargados, "manzana", "M00000001")
    bloques = {b["id"]: b for b in f["bloques"]}
    assert f["contiene_datos_sinteticos"] is True
    assert bloques["estrato_2025"]["distribucion"] == [{"estrato": 2, "etiqueta": "Estrato 2", "n": 1, "porcentaje": 100.0}]
    assert bloques["predios_2026"]["total"] == 10
    assert [d["n"] for d in bloques["predios_2026"]["distribucion"]] == [9, 1]
    assert bloques["estrato_2019"]["estado"] == "no_validada"
    texto = str(f).lower()
    assert "promedio" not in texto and "media" not in texto
    assert any("No mide ingresos" in a for a in f["advertencias_generales"])


def test_ficha_sector_usa_codigo_de_sector(datos_cargados):
    f = fichas.ficha(datos_cargados, "sector", "S0002")
    b = {x["id"]: x for x in f["bloques"]}
    assert f["manzanas_en_unidad"] == 2
    assert b["estrato_2025"]["total"] == 1 and b["estrato_2025"]["manzanas_sin_coincidencia"] == 1
    assert b["predios_2026"]["total"] == 7


def test_ficha_localidad_por_punto_interior(datos_cargados):
    f = fichas.ficha(datos_cargados, "localidad", "99")
    b = {x["id"]: x for x in f["bloques"]}
    assert f["manzanas_en_unidad"] == 4
    assert {d["estrato"]: d["n"] for d in b["estrato_2025"]["distribucion"]} == {2: 1, 3: 2}
    assert b["predios_2026"]["total"] == 17
    assert f["area_ha"] > 0


def test_consistencia(datos_cargados):
    todos = fichas.consistencia(datos_cargados)
    inf = {i["informe"][:20]: i for i in todos[:3]}
    assert len(inf) == 3
    por = {i["informe"]: i for i in todos}
    integ = {f["fuente"]: f for f in por["Integridad de los códigos de manzana"]["fuentes"]}
    assert integ["mr_manzana"]["longitudes"] == {"9": 4} and integ["mr_manzana"]["llaves_duplicadas"] == 0
    assert integ["mr_manzana"]["no_numericos"] == 4  # los códigos sintéticos llevan la letra M
    assert por["Manzanas del Mapa de Referencia sin estrato asignado en 2025"]["ejemplos"] == ["M00000004"]
    assert por["Códigos de estratificación 2025 sin manzana en el Mapa de Referencia"]["registros"] == 0
    l26 = por["Lotes de la tabla de predios 2026 sin relación lote-manzana"]
    assert (l26["registros"], l26["predios_afectados"], l26["ejemplos"]) == (1, 2, ["L999"])
    assert por["Manzanas citadas por la relación lote-manzana que no existen en la capa Manzana"]["registros"] == 0
    codigos = next(i for i in inf.values() if i["informe"].startswith("Códigos"))
    assert codigos["porcentaje"] == 100.0
    lotes = next(i for i in inf.values() if i["informe"].startswith("Lotes"))
    assert (lotes["lotes"], lotes["con_relacion"]) == (4, 3)  # L999 sin relación
    moda = next(i for i in inf.values() if i["informe"].startswith("Manzanas"))
    assert (moda["comparables"], moda["coinciden"]) == (2, 2)
    assert moda["empates_en_estrato_mas_frecuente"] == 0 and moda["limitaciones"]


def test_api(datos_cargados, api_cliente):
    c = api_cliente
    salud = c.get("/api/salud").json()
    assert salud["estado"] == "ok" and salud["esquema_migrado"]
    capas = {x["capa"]: x for x in c.get("/api/capas").json()["capas"]}
    assert capas["localidad"]["habilitada"] and not capas["estrato_2019"]["habilitada"]
    assert capas["localidad"]["sintetico"]
    assert c.get("/api/capas/manzana/geojson").status_code == 400  # exige bbox
    gj = c.get("/api/capas/manzana/geojson", params={"bbox": "-74.2,4.5,-74.0,4.7"}).json()
    assert len(gj["features"]) == 4
    r = c.get("/api/capas/estrato_2019/geojson", params={"bbox": "-74.2,4.5,-74.0,4.7"})
    assert r.status_code == 409 and r.json()["motivos"]
    assert c.get("/api/buscar", params={"q": "sintetico a"}).json()["resultados"][0]["codigo"] == "S0001"
    ficha = c.get("/api/ficha/manzana/M00000001").json()
    assert ficha["codigo"] == "M00000001"
    assert c.get("/api/ficha/manzana/NOEXISTE").status_code == 404
    assert c.get("/api/ficha/barrio/1").status_code == 404
    assert "maplibre" in c.get("/").text
    assert c.get("/web/app.js").status_code == 200


def test_api_sin_datos_muestra_capas_no_validadas(conn, api_cliente):
    capas = api_cliente.get("/api/capas").json()["capas"]
    assert all(not c["habilitada"] for c in capas)
    r = api_cliente.get("/api/ficha/localidad/99")
    assert r.status_code == 409


def _cargar_real(conn, registro, fid, feats):
    """Simula una carga 'real' (sintetico=False) con geometrías de prueba, solo para probar la protección."""
    campos = sorted({k for x in feats for k in x["properties"]})
    return cargar_features(conn, registro["fuentes"][fid], feats, campos, registro["dominio_estrato"],
                           len(feats), [], sintetico=False)


def test_sintetico_no_reemplaza_una_version_real(conn, registro):
    real = _cargar_real(conn, registro, "mr_sector", s.SECTORES)
    r = _cargar(conn, registro, "mr_sector", s.SECTORES)
    assert r["estado"] == "rechazada" and "no la reemplazan" in r["motivo"]
    assert _publicada(conn, "mr_sector") == real["version_id"]
    assert conn.execute("SELECT count(*) AS n FROM geo.version WHERE fuente_id='mr_sector'").fetchone()["n"] == 1


def test_demo_sintetica_se_niega_si_hay_datos_reales(conn, registro, tmp_path):
    from territorial.sinteticos import cargar_demo
    _cargar_real(conn, registro, "mr_localidad", s.LOCALIDADES)
    antes = conn.execute("SELECT count(*) AS n FROM geo.version").fetchone()["n"]
    with pytest.raises(RuntimeError, match="ya hay datos reales"):
        cargar_demo(conn, registro, tmp_path)
    assert conn.execute("SELECT count(*) AS n FROM geo.version").fetchone()["n"] == antes


def test_dato_real_reemplaza_al_sintetico_y_se_marca(conn, registro):
    sint = _cargar(conn, registro, "mr_sector", s.SECTORES)
    real = _cargar_real(conn, registro, "mr_sector", s.SECTORES)
    assert real["estado"] == "publicada" and real["reemplaza"] == sint["version_id"]
    f = next(x for x in fichas.estado_fuentes(conn) if x["id"] == "mr_sector")
    assert f["sintetico"] is False


def test_pagina_usa_maplibre_local_y_no_un_cdn(api_cliente):
    html = api_cliente.get("/").text
    assert "/web/vendor/maplibre-gl-4.7.1/maplibre-gl.js" in html
    assert 'src="http' not in html and 'href="http' not in html  # sin scripts ni estilos externos
    r = api_cliente.get("/web/vendor/maplibre-gl-4.7.1/maplibre-gl.js")
    assert r.status_code == 200 and len(r.content) > 500_000
    assert api_cliente.get("/web/vendor/maplibre-gl-4.7.1/LICENSE.txt").status_code == 200


def test_procedencia_distingue_descarga_verificada_archivo_suelto_y_sintetico(conn, registro, tmp_path):
    import json as _json
    from territorial.carga import cargar_desde_geojson
    from territorial.descargas import ArchivoDescargado, escribir_procedencia, sha256_archivo
    f = registro["fuentes"]["sdp_estrato_manzana_2025"]
    ruta = tmp_path / "estrato.json"
    ruta.write_text(_json.dumps({"type": "FeatureCollection", "features": [
        {"type": "Feature", "properties": x["properties"], "geometry": x["geometry"]} for x in s.ESTRATO_2025]}))
    # 1) archivo suelto: se publica, pero no cuenta como oficial
    r1 = cargar_desde_geojson(conn, f, ruta, registro["dominio_estrato"])
    assert r1["estado"] == "publicada"
    assert any(v["regla"] == "procedencia" and not v["ok"] for v in r1["validaciones"])
    p = {x["fuente"]: x for x in fichas.procedencia(conn)}["sdp_estrato_manzana_2025"]
    assert p["origen"] == "archivo_sin_procedencia" and not p["oficial"]
    # 2) con ficha de procedencia de la URL registrada: descarga verificada
    escribir_procedencia(ArchivoDescargado(ruta, sha256_archivo(ruta), ruta.stat().st_size, f.url, 200), f.id)
    cargar_desde_geojson(conn, f, ruta, registro["dominio_estrato"])
    p = {x["fuente"]: x for x in fichas.procedencia(conn)}["sdp_estrato_manzana_2025"]
    assert p["origen"] == "descarga_verificada" and p["oficial"]
    # 3) archivo modificado después de descargarlo: la huella no coincide
    ruta.write_text(ruta.read_text().replace('"ESTRATO": 2', '"ESTRATO": 3'))
    cargar_desde_geojson(conn, f, ruta, registro["dominio_estrato"])
    p = {x["fuente"]: x for x in fichas.procedencia(conn)}["sdp_estrato_manzana_2025"]
    assert p["origen"] == "archivo_sin_procedencia" and "sha256" in p["detalle"]


def test_procedencia_de_servicio_arcgis(conn, registro):
    import httpx
    import respx
    from territorial.arcgis import ClienteArcGIS
    from territorial.carga import cargar_desde_arcgis
    f = registro["fuentes"]["mr_sector"]
    meta = {"type": "Feature Layer", "capabilities": "Query", "maxRecordCount": 1000, "objectIdField": "OBJECTID",
            "advancedQueryCapabilities": {"supportsPagination": True},
            "fields": [{"name": n} for n in ("OBJECTID", "SCACODIGO", "SCANOMBRE")]}
    with respx.mock:
        respx.get(f.url).respond(json=meta)
        respx.get(f"{f.url}/query", params={"returnCountOnly": "true"}).respond(json={"count": 2})
        respx.get(f"{f.url}/query").respond(json={"type": "FeatureCollection", "features": [
            {"type": "Feature", **x} for x in s.SECTORES]})
        r = cargar_desde_arcgis(conn, f, registro["dominio_estrato"],
                                ClienteArcGIS(httpx.Client(), pausa_entre_paginas=0, dormir=lambda _: None))
    assert r["estado"] == "publicada"
    p = {x["fuente"]: x for x in fichas.procedencia(conn)}["mr_sector"]
    assert p["origen"] == "servicio_oficial" and p["oficial"] and p["registros"] == 2


def test_api_procedencia_de_la_demostracion(datos_cargados, api_cliente):
    filas = {x["fuente"]: x for x in api_cliente.get("/api/procedencia").json()["fuentes"]}
    assert filas["mr_localidad"]["origen"] == "sintetico" and not filas["mr_localidad"]["oficial"]
    assert filas["sdp_estrato_manzana_2019_rest"]["origen"] == "sin_version"


def test_retirar_sinteticos_no_borra_ni_toca_reales(conn, registro):
    from territorial.carga import retirar_sinteticos
    real = _cargar_real(conn, registro, "mr_localidad", s.LOCALIDADES)
    sint = _cargar(conn, registro, "mr_sector", s.SECTORES)
    antes = conn.execute("SELECT count(*) AS n FROM geo.objeto").fetchone()["n"]
    assert retirar_sinteticos(conn) == [{"fuente": "mr_sector", "version_retirada": sint["version_id"]}]
    assert _publicada(conn, "mr_localidad") == real["version_id"]
    assert _publicada(conn, "mr_sector") is None
    assert conn.execute("SELECT estado FROM geo.version WHERE version_id=%s", (sint["version_id"],)).fetchone()["estado"] == "retirada"
    assert conn.execute("SELECT count(*) AS n FROM geo.objeto").fetchone()["n"] == antes
    assert not next(f for f in fichas.estado_fuentes(conn) if f["id"] == "mr_sector")["habilitada"]


def test_informe_fuentes_registra_metadatos_carga_y_estado(conn, registro, tmp_path):
    import json as _json
    from territorial.carga import cargar_desde_geojson
    f = registro["fuentes"]["sdp_estrato_manzana_2025"]
    ruta = tmp_path / "e.json"
    ruta.write_text(_json.dumps({"type": "FeatureCollection", "crs": {"type": "name", "properties": {"name": "EPSG:4686"}},
                                 "features": [{"type": "Feature", **x} for x in s.ESTRATO_2025]}))
    cargar_desde_geojson(conn, f, ruta, registro["dominio_estrato"])
    inf = {x["fuente"]: x for x in fichas.informe_fuentes(conn, registro)}
    e25 = inf["sdp_estrato_manzana_2025"]
    assert e25["estado_final"] == "publicada" and e25["ultima_carga"]["srid"] == 4686
    assert e25["ultima_carga"]["campos_disponibles"] == ["CODIGO_MANZANA", "ESTRATO"]
    assert e25["procedencia"]["origen"] == "archivo_sin_procedencia"
    assert any(v["regla"] == "llave_unica" and v["ok"] for v in e25["ultima_carga"]["validaciones"])
    assert inf["sdp_estrato_manzana_2019_rest"]["estado_final"] == "deshabilitada"
    assert inf["mr_localidad"]["ultima_carga"] is None
