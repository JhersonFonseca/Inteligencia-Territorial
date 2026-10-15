"""Descarga de archivos oficiales y lectura de GeoJSON y CSV comprimidos."""

from __future__ import annotations

import csv
import hashlib
import io
import json
import zipfile
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

from . import config


@dataclass
class ArchivoDescargado:
    ruta: Path
    sha256: str
    bytes: int
    url: str
    status: int


def descargar_archivo(url: str, destino: Path, cliente: httpx.Client | None = None) -> ArchivoDescargado:
    """Descarga a un archivo temporal y lo renombra solo si terminó completo."""
    cliente = cliente or httpx.Client(timeout=300.0, headers={"User-Agent": config.agente_http()},
                                      follow_redirects=True)
    destino.parent.mkdir(parents=True, exist_ok=True)
    parcial = destino.with_suffix(destino.suffix + ".parcial")
    h = hashlib.sha256()
    total = 0
    try:
        with cliente.stream("GET", url) as r:
            r.raise_for_status()
            esperado = r.headers.get("content-length")
            with parcial.open("wb") as f:
                for bloque in r.iter_bytes(1 << 20):
                    f.write(bloque)
                    h.update(bloque)
                    total += len(bloque)
            if esperado is not None and int(esperado) != total:
                raise IOError(f"Descarga incompleta: se esperaban {esperado} bytes y llegaron {total}.")
            status = r.status_code
    except BaseException:
        # Solo se elimina el temporal propio (.parcial); un archivo completo anterior queda intacto.
        parcial.unlink(missing_ok=True)
        raise
    parcial.replace(destino)
    return ArchivoDescargado(destino, h.hexdigest(), total, url, status)


def ruta_procedencia(ruta: Path) -> Path:
    return ruta.with_name(ruta.name + ".procedencia.json")


def escribir_procedencia(a: ArchivoDescargado, fuente_id: str) -> Path:
    """Guarda junto al archivo de dónde y cuándo se descargó, con su huella sha256."""
    from datetime import datetime, timezone
    destino = ruta_procedencia(a.ruta)
    destino.write_text(json.dumps({
        "fuente": fuente_id, "url": a.url, "status_http": a.status, "bytes": a.bytes, "sha256": a.sha256,
        "descargado_en": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "agente": config.agente_http()}, ensure_ascii=False, indent=2), encoding="utf-8")
    return destino


def leer_procedencia(ruta: Path, sha256: str, fuente_id: str, url_oficial: str) -> dict[str, Any]:
    """Comprueba que el archivo es exactamente el que se descargó de la URL oficial registrada."""
    ficha = ruta_procedencia(ruta)
    if not ficha.exists():
        return {"tipo": "archivo_sin_procedencia",
                "motivo": "El archivo no se descargó con 'territorial descargar' (no hay ficha de procedencia)."}
    try:
        datos = json.loads(ficha.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        return {"tipo": "archivo_sin_procedencia", "motivo": f"Ficha de procedencia ilegible: {e}"}
    problemas = []
    if datos.get("sha256") != sha256:
        problemas.append("la huella sha256 no coincide: el archivo cambió después de descargarse")
    if datos.get("url") != url_oficial:
        problemas.append(f"se descargó de {datos.get('url')} y no de la URL registrada")
    if datos.get("fuente") != fuente_id:
        problemas.append(f"la descarga corresponde a la fuente {datos.get('fuente')}")
    if problemas:
        return {"tipo": "archivo_sin_procedencia", "motivo": "; ".join(problemas) + "."}
    return {"tipo": "descarga_verificada", "url": datos["url"], "descargado_en": datos.get("descargado_en"),
            "bytes": datos.get("bytes"), "status_http": datos.get("status_http")}


def sha256_archivo(ruta: Path) -> str:
    h = hashlib.sha256()
    with ruta.open("rb") as f:
        for bloque in iter(lambda: f.read(1 << 20), b""):
            h.update(bloque)
    return h.hexdigest()


def leer_geojson(ruta: Path) -> tuple[list[dict[str, Any]], list[str], int]:
    """Devuelve (features, campos presentes en el primer registro, SRID declarado).

    Sin miembro "crs" se asume WGS84 (EPSG:4326), como establece RFC 7946.
    """
    with ruta.open("r", encoding="utf-8-sig") as f:
        datos = json.load(f)
    if datos.get("type") != "FeatureCollection":
        raise ValueError("El archivo no es un FeatureCollection de GeoJSON.")
    feats = [{"properties": x.get("properties") or {}, "geometry": x.get("geometry")}
             for x in datos.get("features") or []]
    campos = sorted(feats[0]["properties"].keys()) if feats else []
    crs = (datos.get("crs") or {}).get("properties", {}).get("name")
    if crs and not any(c in crs for c in ("4326", "4686", "CRS84")):
        raise ValueError(f"Sistema de referencia no soportado en el GeoJSON: {crs}")
    srid = 4686 if crs and "4686" in crs else 4326
    return feats, campos, srid


def contar_lote_estrato_csv_zip(ruta: Path, campo_lote: str, campo_estrato: str
                                ) -> tuple[Counter, list[str], int]:
    """Lee el CSV dentro del ZIP y cuenta predios por (lote, estrato).

    Solo se leen las dos columnas indicadas: el identificador predial (CHIP) u otras columnas
    no se conservan en memoria ni en la base de datos.
    Devuelve (conteos, columnas del archivo, filas leídas).
    """
    with zipfile.ZipFile(ruta) as z:
        nombres = [n for n in z.namelist() if n.lower().endswith(".csv")]
        if len(nombres) != 1:
            raise ValueError(f"Se esperaba un único CSV en el ZIP y hay {len(nombres)}: {nombres}")
        with z.open(nombres[0]) as crudo:
            muestra = crudo.read(64 * 1024).decode("utf-8-sig", errors="replace")
        separador = csv.Sniffer().sniff(muestra, delimiters=",;|\t").delimiter
        with z.open(nombres[0]) as crudo:
            texto = io.TextIOWrapper(crudo, encoding="utf-8-sig", newline="")
            lector = csv.DictReader(texto, delimiter=separador)
            columnas = list(lector.fieldnames or [])
            faltantes = [c for c in (campo_lote, campo_estrato) if c not in columnas]
            if faltantes:
                return Counter(), columnas, 0
            conteos: Counter = Counter()
            filas = 0
            for fila in lector:
                filas += 1
                conteos[(fila[campo_lote].strip(), fila[campo_estrato].strip())] += 1
    return conteos, columnas, filas
