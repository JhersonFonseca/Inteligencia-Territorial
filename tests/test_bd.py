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
    inf = {i["informe"][:20]: i for i in fichas.consistencia(datos_cargados)}
    assert len(inf) == 3
    codigos = next(i for i in inf.values() if i["informe"].startswith("Códigos"))
    assert codigos["porcentaje"] == 100.0
    lotes = next(i for i in inf.values() if i["informe"].startswith("Lotes"))
    assert (lotes["lotes"], lotes["con_relacion"]) == (4, 3)  # L999 sin relación
    moda = next(i for i in inf.values() if i["informe"].startswith("Manzanas"))
    assert (moda["comparables"], moda["coinciden"]) == (2, 2)


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
