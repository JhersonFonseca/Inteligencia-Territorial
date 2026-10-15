"""API web del módulo territorial (FastAPI) y servidor del mapa.

Solo expone datos de fuentes habilitadas. Una fuente no habilitada responde 409 con sus motivos.
"""

from __future__ import annotations

from contextlib import contextmanager
from importlib import resources
from typing import Iterator

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import __version__, bd, fichas
from .registro import cargar_registro

app = FastAPI(title="Inteligencia territorial — Bogotá", version=__version__)

_WEB = resources.files("territorial") / "web"


@contextmanager
def _conexion() -> Iterator:
    conn = bd.conectar()
    try:
        yield conn
    finally:
        conn.close()


def _no_habilitada(e: fichas.NoHabilitada) -> JSONResponse:
    return JSONResponse(status_code=409, content={
        "detalle": "La fuente no está habilitada para consulta.",
        "fuente": e.fuente.get("id"), "motivos": e.fuente.get("motivos", [])})


def _bbox(texto: str | None) -> tuple[float, float, float, float] | None:
    if not texto:
        return None
    try:
        partes = tuple(float(x) for x in texto.split(","))
    except ValueError:
        raise HTTPException(400, "bbox debe ser minx,miny,maxx,maxy en EPSG:4326") from None
    if len(partes) != 4 or partes[0] >= partes[2] or partes[1] >= partes[3]:
        raise HTTPException(400, "bbox debe ser minx,miny,maxx,maxy en EPSG:4326")
    return partes  # type: ignore[return-value]


@app.get("/api/salud")
def salud():
    try:
        with _conexion() as conn:
            version = bd.postgis_version(conn)
            esquema = conn.execute("SELECT to_regclass('geo.fuente') IS NOT NULL AS ok").fetchone()["ok"]
    except Exception as e:  # noqa: BLE001
        return JSONResponse(status_code=503, content={"estado": "error", "detalle": str(e)})
    return {"estado": "ok", "version": __version__, "postgis": version, "esquema_migrado": esquema}


@app.get("/api/fuentes")
def fuentes():
    with _conexion() as conn:
        lista = fichas.estado_fuentes(conn)
    return {"fecha_verificacion_registro": cargar_registro()["fecha_verificacion"], "fuentes": lista}


@app.get("/api/capas")
def capas():
    with _conexion() as conn:
        por_id = {f["id"]: f for f in fichas.estado_fuentes(conn)}
    salida = []
    for capa, fid in {**fichas.NIVELES, **fichas.CAPAS_ESTRATO}.items():
        f = por_id.get(fid, {"id": fid, "nombre": fid, "habilitada": False,
                             "motivos": ["Fuente no registrada en la base de datos."], "advertencias": []})
        salida.append({"capa": capa, "fuente": fid, "nombre": f.get("nombre"),
                       "habilitada": f["habilitada"], "motivos": f.get("motivos", []),
                       "advertencias": f.get("advertencias", []), "sintetico": f.get("sintetico", False),
                       "requiere_bbox": capa in ("manzana", "estrato_2025", "estrato_2019")})
    return {"capas": salida}


@app.get("/api/capas/{capa}/geojson")
def capa_geojson(capa: str, bbox: str | None = None, limite: int = Query(6000, ge=1, le=20000)):
    caja = _bbox(bbox)
    if capa in ("manzana", "estrato_2025", "estrato_2019") and caja is None:
        raise HTTPException(400, "Esta capa es muy grande: indica bbox=minx,miny,maxx,maxy.")
    try:
        with _conexion() as conn:
            return fichas.capa_geojson(conn, capa, caja, limite)
    except KeyError:
        raise HTTPException(404, f"Capa desconocida: {capa}") from None
    except fichas.NoHabilitada as e:
        return _no_habilitada(e)


@app.get("/api/buscar")
def buscar(q: str = Query(..., min_length=2, max_length=80)):
    with _conexion() as conn:
        return {"resultados": fichas.buscar(conn, q)}


@app.get("/api/ficha/{nivel}/{codigo}")
def ficha(nivel: str, codigo: str):
    try:
        with _conexion() as conn:
            datos = fichas.ficha(conn, nivel, codigo)
    except KeyError:
        raise HTTPException(404, f"Nivel desconocido: {nivel}") from None
    except fichas.NoHabilitada as e:
        return _no_habilitada(e)
    if datos is None:
        raise HTTPException(404, f"No existe {nivel} {codigo} en la versión publicada.")
    return datos


@app.get("/api/consistencia")
def consistencia():
    with _conexion() as conn:
        return {"informes": fichas.consistencia(conn)}


@app.get("/", include_in_schema=False)
def inicio():
    with resources.as_file(_WEB / "index.html") as ruta:
        return FileResponse(ruta)


with resources.as_file(_WEB) as _carpeta_web:
    app.mount("/web", StaticFiles(directory=str(_carpeta_web)), name="web")
