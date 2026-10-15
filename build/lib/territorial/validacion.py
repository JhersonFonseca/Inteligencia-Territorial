"""Reglas de calidad. Cada regla devuelve un Resultado; las bloqueantes impiden publicar."""

from __future__ import annotations

from collections import Counter
from dataclasses import asdict, dataclass
from datetime import date
from typing import Any, Iterable

from . import config


@dataclass
class Resultado:
    regla: str
    ok: bool
    bloqueante: bool
    detalle: str

    def dict(self) -> dict[str, Any]:
        return asdict(self)


def conteo(esperados: int | None, recibidos: int) -> Resultado:
    if esperados is None:
        return Resultado("conteo", True, False,
                         f"La fuente no declara un conteo; se recibieron {recibidos} registros.")
    ok = esperados == recibidos
    return Resultado("conteo", ok, True,
                     f"Esperados {esperados}, recibidos {recibidos}." if ok else
                     f"Pérdida o exceso en la descarga: esperados {esperados}, recibidos {recibidos}.")


def no_vacio(recibidos: int) -> Resultado:
    return Resultado("no_vacio", recibidos > 0, True, f"{recibidos} registros.")


def campos_presentes(requeridos: Iterable[str], disponibles: Iterable[str]) -> Resultado:
    disponibles = list(disponibles)
    faltan = [c for c in requeridos if c not in disponibles]
    return Resultado("campos", not faltan, True,
                     "Todos los campos esperados están presentes." if not faltan else
                     f"Faltan {faltan}. Campos del archivo: {disponibles}. "
                     "Corrige 'campos' en fuentes.yaml tras revisar la fuente.")


def llaves_unicas(llaves: Iterable[Any]) -> Resultado:
    c = Counter(llaves)
    nulas = c.pop(None, 0) + c.pop("", 0)
    duplicadas = {k: n for k, n in c.items() if n > 1}
    ok = not duplicadas and nulas == 0
    detalle = "Sin llaves duplicadas ni vacías."
    if not ok:
        ejemplos = list(duplicadas.items())[:5]
        detalle = f"{len(duplicadas)} llaves duplicadas (ejemplos: {ejemplos}); {nulas} llaves vacías."
    return Resultado("llave_unica", ok, True, detalle)


def dominio_estrato(valores: Iterable[Any], dominio: dict[int, str]) -> Resultado:
    fuera = Counter()
    for v in valores:
        try:
            if int(v) not in dominio:
                fuera[v] += 1
        except (TypeError, ValueError):
            fuera[v] += 1
    ok = not fuera
    return Resultado("dominio_estrato", ok, True,
                     "Todos los valores de estrato están en el dominio publicado." if ok else
                     f"Valores fuera del dominio {sorted(dominio)}: {dict(list(fuera.items())[:10])}")


def _coordenadas(geom: dict[str, Any]) -> Iterable[tuple[float, float]]:
    def recorrer(c):
        if c and isinstance(c[0], (int, float)):
            yield (c[0], c[1])
        else:
            for sub in c:
                yield from recorrer(sub)
    yield from recorrer(geom.get("coordinates") or [])


def geometria_presente_y_tipo(geoms: list[dict[str, Any] | None]) -> Resultado:
    sin = sum(1 for g in geoms if not g)
    tipos = Counter(g.get("type") for g in geoms if g)
    malos = {t: n for t, n in tipos.items() if t not in ("Polygon", "MultiPolygon")}
    ok = sin == 0 and not malos
    return Resultado("geometria_tipo", ok, True,
                     f"{len(geoms)} polígonos." if ok else
                     f"{sin} registros sin geometría; tipos no poligonales: {malos}.")


def dentro_de_bogota(geoms: list[dict[str, Any] | None]) -> Resultado:
    minx, miny, maxx, maxy = config.EXTENSION_BOGOTA
    fuera = 0
    for g in geoms:
        if not g:
            continue
        if any(not (minx <= x <= maxx and miny <= y <= maxy) for x, y in _coordenadas(g)):
            fuera += 1
    return Resultado("extension_bogota", fuera == 0, True,
                     "Todas las geometrías están dentro de la extensión de Bogotá (EPSG:4326)." if fuera == 0 else
                     f"{fuera} geometrías con vértices fuera de la extensión de Bogotá; "
                     "revisa el sistema de referencia.")


def licencia(licencia_verificada: bool, licencia_texto: str | None) -> Resultado:
    ok = bool(licencia_verificada and licencia_texto)
    return Resultado("licencia", ok, False,
                     f"Licencia verificada: {licencia_texto}." if ok else
                     "Licencia no verificada: la versión puede cargarse para revisión interna, "
                     "pero la fuente no se habilita para consulta externa.")


def fecha_dato(fecha: date | None, hoy: date | None = None) -> Resultado:
    hoy = hoy or date.today()
    if fecha is None:
        return Resultado("fecha_dato", False, False, "Fecha del dato desconocida.")
    anios = (hoy - fecha).days / 365.25
    if anios > config.ANIOS_ADVERTENCIA_ANTIGUEDAD:
        return Resultado("fecha_dato", False, False,
                         f"Dato con {anios:.1f} años de antigüedad ({fecha.isoformat()}).")
    return Resultado("fecha_dato", True, False, f"Fecha del dato: {fecha.isoformat()}.")


def hay_bloqueos(resultados: list[Resultado]) -> list[Resultado]:
    return [r for r in resultados if r.bloqueante and not r.ok]
