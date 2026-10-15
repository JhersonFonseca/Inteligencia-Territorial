"""Conector para servicios ArcGIS REST (MapServer / FeatureServer).

- Lee los metadatos de la capa y se niega a extraer capas sin la capacidad Query (solo visuales).
- Pide el conteo antes de descargar y pagina con resultOffset/resultRecordCount ordenando por el
  campo OBJECTID que declara la capa. No supone que los identificadores empiecen en 1.
- Reintenta errores transitorios y registra cada solicitud en una bitácora.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Callable

import httpx

from . import config


class ErrorServicio(RuntimeError):
    """El servicio respondió con un error no recuperable."""


class CapaSoloVisual(ErrorServicio):
    """La capa no permite consultar atributos (solo visualización)."""


@dataclass
class ResultadoDescarga:
    features: list[dict[str, Any]]
    esperados: int
    campos_servicio: list[str]
    bitacora: list[dict[str, Any]] = field(default_factory=list)


class ClienteArcGIS:
    def __init__(self, cliente: httpx.Client | None = None, reintentos: int = 3,
                 pausa_entre_paginas: float = 0.5, dormir: Callable[[float], None] = time.sleep):
        self.http = cliente or httpx.Client(
            timeout=60.0, headers={"User-Agent": config.agente_http()}, follow_redirects=True)
        self.reintentos = reintentos
        self.pausa = pausa_entre_paginas
        self.dormir = dormir
        self.bitacora: list[dict[str, Any]] = []

    # --- solicitudes con reintentos ---------------------------------------------------------
    def _get(self, url: str, params: dict[str, Any]) -> dict[str, Any]:
        ultimo_error: Exception | None = None
        for intento in range(1, self.reintentos + 1):
            inicio = time.monotonic()
            entrada: dict[str, Any] = {"url": url, "params": dict(params), "intento": intento}
            try:
                r = self.http.get(url, params=params)
                entrada["status"] = r.status_code
                entrada["ms"] = round((time.monotonic() - inicio) * 1000)
                if r.status_code >= 500:
                    raise httpx.HTTPStatusError("Error del servidor", request=r.request, response=r)
                if r.status_code >= 400:
                    self.bitacora.append(entrada)
                    raise ErrorServicio(f"HTTP {r.status_code} en {url}")
                datos = r.json()
                if isinstance(datos, dict) and "error" in datos:
                    codigo = int(datos["error"].get("code", 0) or 0)
                    entrada["error_servicio"] = datos["error"]
                    if codigo >= 500:
                        raise httpx.HTTPStatusError("Error del servicio", request=r.request, response=r)
                    self.bitacora.append(entrada)
                    raise ErrorServicio(f"El servicio devolvió un error: {datos['error']}")
                self.bitacora.append(entrada)
                return datos
            except (httpx.TransportError, httpx.HTTPStatusError) as e:
                entrada.setdefault("status", None)
                entrada["error"] = str(e)
                self.bitacora.append(entrada)
                ultimo_error = e
                if intento < self.reintentos:
                    self.dormir(2 ** (intento - 1))
        raise ErrorServicio(f"Falló tras {self.reintentos} intentos: {url} ({ultimo_error})")

    # --- operaciones -------------------------------------------------------------------------
    def metadatos(self, url_capa: str) -> dict[str, Any]:
        datos = self._get(url_capa, {"f": "json"})
        capacidades = datos.get("capabilities", "")
        if "Query" not in capacidades.split(",") or datos.get("type") == "Raster Layer":
            raise CapaSoloVisual(f"La capa no permite consultas de atributos: {url_capa}")
        return datos

    def contar(self, url_capa: str, where: str = "1=1") -> int:
        datos = self._get(f"{url_capa}/query", {"where": where, "returnCountOnly": "true", "f": "json"})
        return int(datos["count"])

    def descargar(self, url_capa: str, campos: list[str], con_geometria: bool = True,
                  where: str = "1=1") -> ResultadoDescarga:
        meta = self.metadatos(url_capa)
        campos_servicio = [c["name"] for c in meta.get("fields") or []]
        faltantes = [c for c in campos if c not in campos_servicio]
        if faltantes:
            raise ErrorServicio(
                f"La capa no tiene los campos esperados {faltantes}. Campos disponibles: {campos_servicio}")
        campo_oid = meta.get("objectIdField") or next(
            (c["name"] for c in meta.get("fields") or [] if c.get("type") == "esriFieldTypeOID"), "OBJECTID")
        tamano = min(int(meta.get("maxRecordCount") or 1000), config.TAMANO_PAGINA_MAXIMO)
        avanzadas = meta.get("advancedQueryCapabilities") or {}
        if avanzadas and not avanzadas.get("supportsPagination", False):
            raise ErrorServicio("La capa no declara soporte de paginación; se requiere otro método de extracción.")

        esperados = self.contar(url_capa, where)
        features: list[dict[str, Any]] = []
        desplazamiento = 0
        while desplazamiento < esperados:
            params = {
                "where": where,
                "outFields": ",".join(campos),
                "orderByFields": campo_oid,
                "resultOffset": desplazamiento,
                "resultRecordCount": tamano,
                "returnGeometry": "true" if con_geometria else "false",
                "f": "geojson" if con_geometria else "json",
            }
            if con_geometria:
                params["outSR"] = 4326
            datos = self._get(f"{url_capa}/query", params)
            pagina = _normalizar_pagina(datos, con_geometria)
            if not pagina:
                break  # El servicio no devolvió más registros; el conteo se valida después.
            features.extend(pagina)
            desplazamiento += len(pagina)
            if desplazamiento < esperados and self.pausa:
                self.dormir(self.pausa)
        return ResultadoDescarga(features=features, esperados=esperados,
                                 campos_servicio=campos_servicio, bitacora=list(self.bitacora))


def _normalizar_pagina(datos: dict[str, Any], con_geometria: bool) -> list[dict[str, Any]]:
    """Convierte GeoJSON o JSON de Esri en una lista de {properties, geometry}."""
    feats = datos.get("features") or []
    if con_geometria:
        return [{"properties": f.get("properties") or {}, "geometry": f.get("geometry")} for f in feats]
    return [{"properties": f.get("attributes") or {}, "geometry": None} for f in feats]
