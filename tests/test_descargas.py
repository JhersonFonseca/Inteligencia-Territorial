import json

import httpx
import pytest
import respx

from territorial.descargas import contar_lote_estrato_csv_zip, descargar_archivo, leer_geojson

from .conftest import zip_csv


def test_csv_agrega_por_lote_y_estrato_sin_conservar_chip(tmp_path):
    ruta = zip_csv(tmp_path / "x.zip", [("L1", "2"), ("L1", "2"), ("L1", "3"), ("L2", "1")])
    conteos, columnas, filas = contar_lote_estrato_csv_zip(ruta, "ESOCLOTE", "ESOESTRATO")
    assert filas == 4 and "ESOCHIP" in columnas
    assert conteos == {("L1", "2"): 2, ("L1", "3"): 1, ("L2", "1"): 1}
    assert not any("AAA" in str(k) for k in conteos)  # el CHIP no se conserva


def test_csv_con_coma_como_separador(tmp_path):
    ruta = zip_csv(tmp_path / "x.zip", [("L1", "2")], separador=",")
    assert contar_lote_estrato_csv_zip(ruta, "ESOCLOTE", "ESOESTRATO")[0] == {("L1", "2"): 1}


def test_csv_sin_columnas_esperadas(tmp_path):
    ruta = zip_csv(tmp_path / "x.zip", [("L1", "2")], columnas=("ESOCHIP", "OTRO", "ESOESTRATO"))
    conteos, columnas, filas = contar_lote_estrato_csv_zip(ruta, "ESOCLOTE", "ESOESTRATO")
    assert not conteos and filas == 0 and "OTRO" in columnas


def test_geojson_detecta_srid(tmp_path):
    base = {"type": "FeatureCollection", "features": [{"type": "Feature", "properties": {"A": 1}, "geometry": None}]}
    p = tmp_path / "a.json"
    p.write_text(json.dumps(base))
    assert leer_geojson(p)[1:] == (["A"], 4326)
    p.write_text(json.dumps({**base, "crs": {"type": "name", "properties": {"name": "EPSG:4686"}}}))
    assert leer_geojson(p)[2] == 4686
    p.write_text(json.dumps({**base, "crs": {"type": "name", "properties": {"name": "EPSG:9377"}}}))
    with pytest.raises(ValueError):
        leer_geojson(p)


@respx.mock
def test_descarga_incompleta_no_deja_archivo_final(tmp_path):
    respx.get("https://x/archivo").respond(200, content=b"12345", headers={"content-length": "10"})
    destino = tmp_path / "archivo.zip"
    with pytest.raises(IOError):
        descargar_archivo("https://x/archivo", destino, httpx.Client())
    assert not destino.exists()


@respx.mock
def test_descarga_completa_calcula_hash(tmp_path):
    respx.get("https://x/archivo").respond(200, content=b"hola")
    r = descargar_archivo("https://x/archivo", tmp_path / "a.bin", httpx.Client())
    assert r.bytes == 4 and r.sha256.startswith("b221d9db")


@respx.mock
def test_fallo_de_red_no_deja_temporal_ni_toca_el_archivo_anterior(tmp_path):
    destino = tmp_path / "archivo.zip"
    destino.write_bytes(b"version anterior")
    respx.get("https://x/archivo").mock(side_effect=httpx.ConnectError("sin red"))
    with pytest.raises(httpx.ConnectError):
        descargar_archivo("https://x/archivo", destino, httpx.Client())
    assert destino.read_bytes() == b"version anterior"
    assert not (tmp_path / "archivo.zip.parcial").exists()


def test_cli_descargar_informa_la_causa_sin_traceback(monkeypatch, capsys):
    from territorial import cli
    def falla(url, destino, cliente=None):
        raise httpx.ProxyError("403 Forbidden")
    monkeypatch.setattr(cli, "descargar_archivo", falla)
    with pytest.raises(SystemExit) as e:
        cli.main(["descargar", "sdp_estrato_manzana_2025"])
    assert "proxy o la red local" in str(e.value.code) and "No se modificó" in str(e.value.code)
