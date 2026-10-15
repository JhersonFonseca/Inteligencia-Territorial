"""Conector ArcGIS REST con respuestas simuladas (sin red)."""
import httpx
import pytest
import respx

from territorial.arcgis import CapaSoloVisual, ClienteArcGIS, ErrorServicio

URL = "https://servicio.ejemplo/arcgis/rest/services/X/MapServer/40"


def meta(max_records=3, capacidades="Map,Query,Data", paginacion=True, tipo="Feature Layer"):
    return {"type": tipo, "capabilities": capacidades, "maxRecordCount": max_records,
            "objectIdField": "OBJECTID",
            "advancedQueryCapabilities": {"supportsPagination": paginacion},
            "fields": [{"name": "OBJECTID", "type": "esriFieldTypeOID"},
                       {"name": "MANCODIGO", "type": "esriFieldTypeString"}]}


def pagina(desde, hasta):
    # Identificadores que NO empiezan en 1 (como en el servicio real, ~616000).
    return {"type": "FeatureCollection", "features": [
        {"type": "Feature", "properties": {"MANCODIGO": f"M{i}"},
         "geometry": {"type": "Polygon", "coordinates": [[[-74.1, 4.6], [-74.09, 4.6], [-74.09, 4.61], [-74.1, 4.6]]]}}
        for i in range(616000 + desde, 616000 + hasta)]}


def cliente():
    return ClienteArcGIS(httpx.Client(), pausa_entre_paginas=0, dormir=lambda s: None)


@respx.mock
def test_pagina_por_offset_ordenado_por_oid_hasta_el_conteo():
    respx.get(URL).respond(json=meta())

    def consulta(request):
        p = request.url.params
        if p.get("returnCountOnly") == "true":
            return httpx.Response(200, json={"count": 7})
        assert p["orderByFields"] == "OBJECTID"
        assert p["outSR"] == "4326" and p["f"] == "geojson"
        off, n = int(p["resultOffset"]), int(p["resultRecordCount"])
        assert n == 3
        return httpx.Response(200, json=pagina(off, min(off + n, 7)))

    ruta = respx.get(f"{URL}/query").mock(side_effect=consulta)
    r = cliente().descargar(URL, ["MANCODIGO"])
    assert r.esperados == 7 and len(r.features) == 7
    assert [f["properties"]["MANCODIGO"] for f in r.features][0] == "M616000"
    offsets = [c.request.url.params.get("resultOffset") for c in ruta.calls]
    assert offsets == [None, "0", "3", "6"]


@respx.mock
def test_tamano_de_pagina_limitado_a_2000():
    respx.get(URL).respond(json=meta(max_records=5000))
    vistos = []

    def consulta(request):
        p = request.url.params
        if p.get("returnCountOnly") == "true":
            return httpx.Response(200, json={"count": 1})
        vistos.append(int(p["resultRecordCount"]))
        return httpx.Response(200, json=pagina(0, 1))

    respx.get(f"{URL}/query").mock(side_effect=consulta)
    cliente().descargar(URL, ["MANCODIGO"])
    assert vistos == [2000]


@respx.mock
def test_perdida_de_registros_se_detecta_por_conteo():
    respx.get(URL).respond(json=meta())

    def consulta(request):
        p = request.url.params
        if p.get("returnCountOnly") == "true":
            return httpx.Response(200, json={"count": 7})
        off = int(p["resultOffset"])
        return httpx.Response(200, json=pagina(off, off + 3) if off == 0 else {"type": "FeatureCollection", "features": []})

    respx.get(f"{URL}/query").mock(side_effect=consulta)
    r = cliente().descargar(URL, ["MANCODIGO"])
    assert r.esperados == 7 and len(r.features) == 3  # la validación 'conteo' lo bloqueará


@respx.mock
def test_reintenta_errores_transitorios_y_registra_bitacora():
    respx.get(URL).mock(side_effect=[httpx.Response(503), httpx.ConnectError("caída"), httpx.Response(200, json=meta())])
    c = cliente()
    assert c.metadatos(URL)["capabilities"]
    assert [e.get("status") for e in c.bitacora] == [503, None, 200]


@respx.mock
def test_error_de_servicio_en_json_con_codigo_500_se_reintenta():
    respx.get(URL).mock(side_effect=[httpx.Response(200, json={"error": {"code": 500, "message": "x"}}),
                                     httpx.Response(200, json=meta())])
    assert cliente().metadatos(URL)


@respx.mock
def test_error_400_no_se_reintenta():
    ruta = respx.get(URL).respond(400)
    with pytest.raises(ErrorServicio):
        cliente().metadatos(URL)
    assert ruta.call_count == 1


@respx.mock
def test_falla_tras_agotar_reintentos():
    respx.get(URL).respond(502)
    with pytest.raises(ErrorServicio, match="Falló tras 3 intentos"):
        cliente().metadatos(URL)


@respx.mock
@pytest.mark.parametrize("m", [meta(capacidades="Map"), meta(tipo="Raster Layer")])
def test_rechaza_capas_solo_visuales(m):
    respx.get(URL).respond(json=m)
    with pytest.raises(CapaSoloVisual):
        cliente().descargar(URL, ["MANCODIGO"])


@respx.mock
def test_rechaza_campos_inexistentes():
    respx.get(URL).respond(json=meta())
    with pytest.raises(ErrorServicio, match="no tiene los campos"):
        cliente().descargar(URL, ["CAMPO_INVENTADO"])


@respx.mock
def test_rechaza_capas_sin_paginacion():
    respx.get(URL).respond(json=meta(paginacion=False))
    with pytest.raises(ErrorServicio, match="paginación"):
        cliente().descargar(URL, ["MANCODIGO"])


@respx.mock
def test_tabla_sin_geometria_usa_json_de_esri():
    respx.get(URL).respond(json=meta())

    def consulta(request):
        p = request.url.params
        if p.get("returnCountOnly") == "true":
            return httpx.Response(200, json={"count": 1})
        assert p["f"] == "json" and p["returnGeometry"] == "false" and "outSR" not in p
        return httpx.Response(200, json={"features": [{"attributes": {"MANCODIGO": "M1"}}]})

    respx.get(f"{URL}/query").mock(side_effect=consulta)
    r = cliente().descargar(URL, ["MANCODIGO"], con_geometria=False)
    assert r.features == [{"properties": {"MANCODIGO": "M1"}, "geometry": None}]
