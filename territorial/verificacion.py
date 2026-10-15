"""Reglas de la verificación de extremo a extremo (las usa herramientas/verificar_mapa.py).

Dos tipos de evidencia que no se mezclan:
- "reales": solo aprueba si cada capa que se muestra procede de un servicio oficial o de una descarga
  verificada de la URL registrada, sin ninguna versión sintética, y si la interfaz lo refleja.
- "sinteticos": solo aprueba si los datos están marcados como sintéticos y la advertencia es visible.

En ambos casos, las fuentes con licencia o validaciones pendientes deben seguir deshabilitadas.
Cada regla devuelve {"comprobacion", "ok", "detalle"}.
"""

from __future__ import annotations

from typing import Any

from . import sinteticos

# Fuentes que debe mostrar el mapa con datos reales (la capa histórica de 2019 queda fuera: bloqueada).
FUENTES_REQUERIDAS = ("mr_localidad", "mr_upl", "mr_sector", "mr_manzana", "mr_lote_manzana",
                      "sdp_estrato_manzana_2025", "sdp_estrato_predio_2026")
ORIGENES_OFICIALES = ("servicio_oficial", "descarga_verificada")

CODIGOS_SINTETICOS = frozenset(
    str(f["properties"].get(c)) for lista, c in (
        (sinteticos.LOCALIDADES, "LOCCODIGO"), (sinteticos.UPLS, "CODIGO_UPL"),
        (sinteticos.SECTORES, "SCACODIGO"), (sinteticos.MANZANAS, "MANCODIGO"))
    for f in lista)


def _r(nombre: str, ok: bool, detalle: str = "") -> dict[str, Any]:
    return {"comprobacion": nombre, "ok": bool(ok), "detalle": detalle}


def _pendiente_bloqueante(f: dict[str, Any]) -> bool:
    return any(p.get("bloqueante") for p in f.get("pendientes") or [])


def comprobar_fuentes(modo: str, fuentes: list[dict], procedencia: list[dict]) -> list[dict]:
    """fuentes: respuesta de /api/fuentes; procedencia: respuesta de /api/procedencia."""
    res = []
    por_id = {f["id"]: f for f in fuentes}
    proc = {p["fuente"]: p for p in procedencia}

    # Fuentes con licencia o validaciones pendientes: siempre deshabilitadas
    indebidas = [f["id"] for f in fuentes
                 if f["habilitada"] and (not f["licencia_verificada"] or not f["licencia"] or _pendiente_bloqueante(f))]
    pendientes = [f["id"] for f in fuentes if not f["licencia_verificada"] or _pendiente_bloqueante(f)]
    res.append(_r("Fuentes con licencia o validaciones pendientes siguen deshabilitadas", not indebidas,
                  f"habilitadas indebidamente: {indebidas}" if indebidas else f"deshabilitadas: {pendientes or 'ninguna'}"))

    if modo == "reales":
        for fid in FUENTES_REQUERIDAS:
            f, p = por_id.get(fid), proc.get(fid, {})
            if not f:
                res.append(_r(f"Procedencia oficial: {fid}", False, "fuente no registrada en la base"))
                continue
            if not f["habilitada"]:
                ultima = f.get("ultima_version") or {}
                causa = "; ".join(f["motivos"])
                if ultima.get("estado") == "cuarentena":
                    causa += f" Última carga en cuarentena (versión {ultima.get('id')}): {ultima.get('motivo')}"
                res.append(_r(f"Procedencia oficial: {fid}", False, f"no habilitada. {causa}"))
                continue
            ok = p.get("oficial") is True and p.get("sintetico") is False
            res.append(_r(f"Procedencia oficial: {fid}", ok,
                          f"{p.get('origen')} · versión {p.get('version_publicada')} · {p.get('detalle')}"))
        sint = [p["fuente"] for p in procedencia if p.get("sintetico")]
        res.append(_r("Ninguna versión publicada es sintética", not sint,
                      f"sintéticas: {sint}" if sint else "todas las versiones publicadas son no sintéticas"))
    else:
        sint = [p["fuente"] for p in procedencia if p.get("sintetico")]
        res.append(_r("La demostración está marcada como sintética en la base", bool(sint),
                      f"versiones sintéticas: {sint}" if sint else "no hay versiones sintéticas publicadas"))
    return res


def comprobar_interfaz(modo: str, estado_ui: dict, franja_visible: bool) -> list[dict]:
    capas_sint = [c["nombre"] for c in estado_ui["capas"].values() if c.get("sintetico")]
    cargadas = [c["nombre"] for c in estado_ui["capas"].values() if c.get("estado") == "cargada" and c.get("n")]
    if modo == "reales":
        return [
            _r("Sin franja de datos sintéticos", not franja_visible, f"franja visible: {franja_visible}"),
            _r("Ninguna capa del mapa marcada como sintética", not capas_sint,
               f"capas sintéticas: {capas_sint}" if capas_sint else
               f"capas cargadas sin marca sintética: {cargadas}" if cargadas else "no hay capas con datos cargadas"),
        ]
    return [_r("Franja de datos sintéticos visible", franja_visible and bool(capas_sint),
               f"franja visible: {franja_visible}; capas sintéticas: {capas_sint}")]


def comprobar_ficha(modo: str, ficha: dict, procedencia: list[dict], etiqueta: str) -> list[dict]:
    """Comprueba que la ficha mostrada usa exactamente las versiones publicadas de origen oficial."""
    if modo != "reales":
        return [_r(f"Ficha {etiqueta} marcada como sintética", ficha.get("contiene_datos_sinteticos") is True)]
    proc = {p["fuente"]: p for p in procedencia}
    res = [
        _r(f"Ficha {etiqueta}: sin datos sintéticos", ficha.get("contiene_datos_sinteticos") is False),
        _r(f"Ficha {etiqueta}: código no pertenece a la demostración",
           str(ficha.get("codigo")) not in CODIGOS_SINTETICOS, f"código {ficha.get('codigo')}"),
    ]
    referencias = [("límite", ficha.get("fuente_unidad"))]
    for b in ficha.get("bloques", []):
        if b.get("estado") == "no_validada":
            continue
        referencias.append((b["id"], b.get("fuente")))
        if b.get("fuente_relacion"):
            referencias.append((f"{b['id']} (relación)", b["fuente_relacion"]))
    for nombre, ref in referencias:
        if not ref:
            continue
        p = proc.get(ref["id"], {})
        ok = (not ref.get("sintetico") and p.get("oficial") is True
              and ref.get("version_publicada") == p.get("version_publicada"))
        res.append(_r(f"Ficha {etiqueta}: {nombre} con versión oficial", ok,
                      f"{ref['id']} versión {ref.get('version_publicada')} · origen {p.get('origen')}"))
    hist = next((b for b in ficha.get("bloques", []) if b["id"] == "estrato_2019"), None)
    if hist is not None and not proc.get("sdp_estrato_manzana_2019_rest", {}).get("habilitada"):
        res.append(_r(f"Ficha {etiqueta}: capa 2019 mostrada como no validada", hist.get("estado") == "no_validada"))
    return res
