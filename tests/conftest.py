import csv
import io
import os
import zipfile

import pytest

from territorial import bd
from territorial.registro import cargar_registro, sincronizar

from territorial import sinteticos as s


@pytest.fixture
def registro():
    return cargar_registro()


def _url_pruebas():
    url = os.environ.get("TERRITORIAL_DB_PRUEBAS")
    if not url:
        pytest.skip("Define TERRITORIAL_DB_PRUEBAS para ejecutar las pruebas con PostGIS.")
    # Salvaguarda: estas pruebas reinician el esquema 'geo'. Solo se permiten en una base cuyo
    # nombre termine en _pruebas, nunca en la base de trabajo ni en Railway.
    nombre = url.rsplit("/", 1)[-1].split("?")[0]
    if not nombre.endswith("_pruebas"):
        pytest.exit(f"TERRITORIAL_DB_PRUEBAS debe apuntar a una base *_pruebas (recibido: {nombre}).")
    return url


@pytest.fixture
def conn(registro):
    c = bd.conectar(_url_pruebas())
    c.execute("DROP SCHEMA IF EXISTS geo CASCADE")  # solo en la base *_pruebas
    c.commit()
    bd.migrar(c)
    sincronizar(c, registro)
    yield c
    c.close()


@pytest.fixture
def api_cliente(conn, monkeypatch):
    from fastapi.testclient import TestClient
    from territorial import api
    monkeypatch.setenv("TERRITORIAL_DB", _url_pruebas())
    return TestClient(api.app)


def zip_csv(ruta, filas, columnas=("ESOCHIP", "ESOCLOTE", "ESOESTRATO"), separador=";"):
    """Crea un ZIP con un CSV sintético. La columna CHIP es inventada y no debe conservarse."""
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=separador)
    w.writerow(columnas)
    for i, (lote, estrato) in enumerate(filas):
        fila = {"ESOCHIP": f"AAA{i:07d}", "ESOCLOTE": lote, "ESOESTRATO": estrato}
        w.writerow([fila.get(c, "") for c in columnas])
    with zipfile.ZipFile(ruta, "w") as z:
        z.writestr("esoc.csv", buf.getvalue())
    return ruta


@pytest.fixture
def datos_cargados(conn, registro, tmp_path):
    """Carga todas las capas sintéticas con el flujo real de validación y publicación."""
    from territorial.carga import cargar_desde_csv_zip, cargar_features
    f, dom = registro["fuentes"], registro["dominio_estrato"]

    def cargar(fid, feats, con_geom=True):
        campos = sorted({k for x in feats for k in x["properties"]})
        r = cargar_features(conn, f[fid], feats, campos, dom, len(feats), [], con_geometria=con_geom,
                            sintetico=True)
        assert r["estado"] == "publicada", r
        return r

    cargar("mr_localidad", s.LOCALIDADES)
    cargar("mr_upl", s.UPLS)
    cargar("mr_sector", s.SECTORES)
    cargar("mr_manzana", s.MANZANAS)
    cargar("sdp_estrato_manzana_2025", s.ESTRATO_2025)
    cargar("mr_lote_manzana", s.LOTES, con_geom=False)
    r = cargar_desde_csv_zip(conn, f["sdp_estrato_predio_2026"], zip_csv(tmp_path / "p.zip", s.PREDIOS_2026),
                             dom, sintetico=True)
    assert r["estado"] == "publicada", r
    return conn
