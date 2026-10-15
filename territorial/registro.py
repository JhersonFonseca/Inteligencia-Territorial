"""Registro de fuentes: lectura de fuentes.yaml, sincronización y reglas de habilitación."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date
from importlib import resources
from typing import Any

import yaml

from . import config

TIPOS_VALIDOS = {"arcgis_rest", "arcgis_tabla", "descarga_geojson", "descarga_csv_zip"}


@dataclass
class Fuente:
    id: str
    nombre: str
    nivel: str
    entidad: str
    tipo: str
    url: str
    ficha_url: str | None
    licencia: str | None
    licencia_verificada: bool
    fecha_dato: date | None
    fecha_dato_origen: str | None
    campos: dict[str, str]
    advertencias: list[str] = field(default_factory=list)
    pendientes: list[dict[str, Any]] = field(default_factory=list)
    conteo_verificado: int | None = None


def _fecha(valor: Any) -> date | None:
    if valor in (None, ""):
        return None
    if isinstance(valor, date):
        return valor
    return date.fromisoformat(str(valor))


def cargar_registro(texto: str | None = None) -> dict[str, Any]:
    if texto is None:
        texto = (resources.files("territorial") / "fuentes.yaml").read_text(encoding="utf-8")
    datos = yaml.safe_load(texto)
    fuentes = []
    ids = set()
    for f in datos["fuentes"]:
        if f["id"] in ids:
            raise ValueError(f"Fuente duplicada en el registro: {f['id']}")
        if f["tipo"] not in TIPOS_VALIDOS:
            raise ValueError(f"Tipo de fuente desconocido en {f['id']}: {f['tipo']}")
        ids.add(f["id"])
        fuentes.append(
            Fuente(
                id=f["id"],
                nombre=f["nombre"],
                nivel=f["nivel"],
                entidad=f["entidad"],
                tipo=f["tipo"],
                url=f["url"],
                ficha_url=f.get("ficha_url"),
                licencia=f.get("licencia"),
                licencia_verificada=bool(f.get("licencia_verificada", False)),
                fecha_dato=_fecha(f.get("fecha_dato")),
                fecha_dato_origen=f.get("fecha_dato_origen"),
                campos=dict(f.get("campos") or {}),
                advertencias=list(f.get("advertencias") or []),
                pendientes=list(f.get("pendientes") or []),
                conteo_verificado=f.get("conteo_verificado"),
            )
        )
    dominio = {int(k): v for k, v in datos["dominio_estrato"].items()}
    return {"fuentes": {f.id: f for f in fuentes}, "dominio_estrato": dominio,
            "fecha_verificacion": datos.get("fecha_verificacion")}


def sincronizar(conn, registro: dict[str, Any] | None = None) -> int:
    """Copia el registro a geo.fuente. Nunca toca version_publicada."""
    registro = registro or cargar_registro()
    for f in registro["fuentes"].values():
        conn.execute(
            """
            INSERT INTO geo.fuente (fuente_id, nombre, nivel, entidad, tipo, url, ficha_url, licencia,
                                    licencia_verificada, fecha_dato, fecha_dato_origen, campos,
                                    advertencias, pendientes, sincronizada_en)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s, now())
            ON CONFLICT (fuente_id) DO UPDATE SET
              nombre=EXCLUDED.nombre, nivel=EXCLUDED.nivel, entidad=EXCLUDED.entidad,
              tipo=EXCLUDED.tipo, url=EXCLUDED.url, ficha_url=EXCLUDED.ficha_url,
              licencia=EXCLUDED.licencia, licencia_verificada=EXCLUDED.licencia_verificada,
              fecha_dato=EXCLUDED.fecha_dato, fecha_dato_origen=EXCLUDED.fecha_dato_origen,
              campos=EXCLUDED.campos, advertencias=EXCLUDED.advertencias,
              pendientes=EXCLUDED.pendientes, sincronizada_en=now()
            """,
            (f.id, f.nombre, f.nivel, f.entidad, f.tipo, f.url, f.ficha_url, f.licencia,
             f.licencia_verificada, f.fecha_dato, f.fecha_dato_origen, json.dumps(f.campos),
             json.dumps(f.advertencias, ensure_ascii=False),
             json.dumps(f.pendientes, ensure_ascii=False)),
        )
    conn.commit()
    return len(registro["fuentes"])


def motivos_no_habilitada(licencia_verificada: bool, licencia: str | None,
                          pendientes: list[dict[str, Any]], version_publicada: int | None) -> list[str]:
    """Razones por las que una fuente NO puede consultarse externamente. Lista vacía = habilitada."""
    motivos = []
    if not licencia_verificada or not licencia:
        motivos.append("Licencia no verificada.")
    for p in pendientes or []:
        if p.get("bloqueante"):
            motivos.append(f"Pendiente bloqueante: {p.get('texto')}")
    if version_publicada is None:
        motivos.append("No hay una versión que haya pasado todas las validaciones bloqueantes.")
    return motivos


def advertencias_fecha(fecha_dato: date | None, hoy: date | None = None) -> list[str]:
    hoy = hoy or date.today()
    if fecha_dato is None:
        return ["Fecha del dato desconocida."]
    anios = (hoy - fecha_dato).days / 365.25
    if anios > config.ANIOS_ADVERTENCIA_ANTIGUEDAD:
        return [f"El dato tiene más de {config.ANIOS_ADVERTENCIA_ANTIGUEDAD} años (fecha del dato: {fecha_dato.isoformat()})."]
    return []
