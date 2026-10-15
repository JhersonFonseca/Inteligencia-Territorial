"""Pruebas de integración con los servicios oficiales. Requieren Internet.

Ejecutar desde tu computador:   pytest -m red -v
Nada de esto se ejecutó en el entorno de desarrollo (sin acceso a los servidores de Bogotá).
Estas pruebas solo LEEN: no cargan datos ni modifican la base.
"""
import json
import zipfile

import httpx
import pytest

from territorial.arcgis import ClienteArcGIS
from territorial.descargas import contar_lote_estrato_csv_zip, descargar_archivo, leer_geojson
from territorial.registro import cargar_registro

pytestmark = pytest.mark.red

REG = cargar_registro()
F = REG["fuentes"]
ARCGIS = [f for f in F.values() if f.tipo in ("arcgis_rest", "arcgis_tabla")]


@pytest.fixture(scope="module")
def cliente():
    return ClienteArcGIS(pausa_entre_paginas=0.2)


@pytest.mark.parametrize("fuente", ARCGIS, ids=[f.id for f in ARCGIS])
def test_capa_arcgis_consultable_con_campos_y_paginacion(cliente, fuente):
    meta = cliente.metadatos(fuente.url)  # falla si es solo visual
    campos = {c["name"] for c in meta["fields"]}
    faltan = set(fuente.campos.values()) - campos
    assert not faltan, f"{fuente.id}: faltan {faltan}; disponibles {sorted(campos)}"
    assert (meta.get("advancedQueryCapabilities") or {}).get("supportsPagination"), "sin paginación"
    n = cliente.contar(fuente.url)
    print(f"\n{fuente.id}: {n} registros, maxRecordCount={meta.get('maxRecordCount')}")
    assert n > 0
    if fuente.conteo_verificado is not None:
        assert n == fuente.conteo_verificado, "El conteo cambió desde la verificación; revisa la fuente."


def test_2019_suma_por_estrato_igual_al_total(cliente):
    f = F["sdp_estrato_manzana_2019_rest"]
    total = cliente.contar(f.url)
    por_estrato = {e: cliente.contar(f.url, f"{f.campos['estrato']}={e}") for e in REG["dominio_estrato"]}
    print("\n2019 por estrato:", por_estrato, "total:", total)
    assert sum(por_estrato.values()) == total, "Hay valores de estrato fuera del dominio publicado."


def test_paginacion_real_de_una_muestra(cliente):
    """Descarga los primeros sectores con páginas pequeñas y verifica que no se repiten llaves."""
    f = F["mr_sector"]
    c = ClienteArcGIS(pausa_entre_paginas=0.2)
    meta = c.metadatos(f.url)
    llave = f.campos["llave"]
    vistos = []
    for offset in (0, 50, 100):
        datos = c._get(f"{f.url}/query", {"where": "1=1", "outFields": llave, "orderByFields": meta["objectIdField"],
                                          "resultOffset": offset, "resultRecordCount": 50,
                                          "returnGeometry": "false", "f": "json"})
        vistos += [x["attributes"][llave] for x in datos["features"]]
    assert len(vistos) == len(set(vistos)) == 150


@pytest.fixture(scope="module")
def carpeta(tmp_path_factory):
    return tmp_path_factory.mktemp("descargas")


def test_descarga_2025_geojson_campos_y_crs(carpeta):
    f = F["sdp_estrato_manzana_2025"]
    r = descargar_archivo(f.url, carpeta / "estrato2025.json")
    feats, campos, srid = leer_geojson(r.ruta)
    print(f"\n2025: {len(feats)} manzanas, SRID {srid}, campos {campos}, sha256 {r.sha256}")
    faltan = set(f.campos.values()) - set(campos)
    assert not faltan, f"Ajusta 'campos' de {f.id} en fuentes.yaml: faltan {faltan}; el archivo trae {campos}"
    longitudes = {len(str(x["properties"][f.campos["llave"]])) for x in feats[:2000]}
    print("Longitudes de CODIGO_MANZANA en la muestra:", longitudes)


def test_descarga_2026_csv_columnas(carpeta):
    f = F["sdp_estrato_predio_2026"]
    r = descargar_archivo(f.url, carpeta / "esoc.csv.zip")
    with zipfile.ZipFile(r.ruta) as z:
        print("\nArchivos en el ZIP:", z.namelist())
    conteos, columnas, filas = contar_lote_estrato_csv_zip(r.ruta, f.campos["lote"], f.campos["estrato"])
    print(f"2026: {filas} filas, {len(conteos)} pares lote-estrato, columnas {columnas}, sha256 {r.sha256}")
    assert conteos, f"Ajusta 'campos' de {f.id}: el archivo trae {columnas}"
