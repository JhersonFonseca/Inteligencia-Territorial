"""Verificación automática del mapa en un navegador real (Chromium sin interfaz).

Registra la consola y la red, separando las solicitudes bloqueadas a propósito de los errores
reales, y comprueba el zoom, el buscador, la selección con clic y la actualización de la ficha.
Guarda capturas y un informe JSON en la carpeta de salida.

Uso:
    pip install playwright && python -m playwright install chromium
    python herramientas/verificar_mapa.py --url http://localhost:8000 --buscar CHAPINERO --salida evidencia/real

Modos del mapa base (--mapa-base):
    real          las teselas se piden a OpenStreetMap normalmente (uso en tu computador)
    bloqueado     se bloquean a propósito (simula una red sin acceso al proveedor)
    sin-respuesta las solicitudes quedan colgadas (simula un proveedor que no responde)
    simulado      se responden con una tesela gris generada localmente (comprueba el estado "disponible";
                  NO es cartografía real y el informe lo indica)
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import struct
import sys
import zlib
from datetime import datetime
from pathlib import Path

HOST_TESELAS = "tile.openstreetmap.org"


def _png_gris(lado: int = 256, gris: int = 214) -> bytes:
    """Tesela PNG gris uniforme generada localmente. Solo para el modo "simulado"."""
    def bloque(tipo: bytes, datos: bytes) -> bytes:
        return struct.pack(">I", len(datos)) + tipo + datos + struct.pack(">I", zlib.crc32(tipo + datos))
    filas = b"".join(b"\x00" + bytes([gris]) * lado for _ in range(lado))
    return (b"\x89PNG\r\n\x1a\n" + bloque(b"IHDR", struct.pack(">IIBBBBB", lado, lado, 8, 0, 0, 0, 0))
            + bloque(b"IDAT", zlib.compress(filas)) + bloque(b"IEND", b""))


TESELA_SIMULADA = _png_gris()


def clasificar(url: str, modo: str, resultado=None) -> str:
    if HOST_TESELAS in url and resultado == "net::ERR_ABORTED" and modo != "bloqueado":
        # MapLibre cancela las teselas que dejan de hacer falta al mover o acercar el mapa.
        return "mapa_base_cancelada_por_el_mapa"
    if HOST_TESELAS in url:
        return "mapa_base_bloqueado_a_proposito" if modo in ("bloqueado", "sin-respuesta") else \
            "mapa_base_simulado" if modo == "simulado" else "mapa_base"
    if "/api/" in url:
        return "api"
    if "/web/" in url or re.match(r"https?://[^/]+/$", url):
        return "recurso_local"
    return "externo"


async def verificar(args) -> dict:
    from playwright.async_api import async_playwright

    salida = Path(args.salida)
    salida.mkdir(parents=True, exist_ok=True)
    consola, red, comprobaciones = [], [], []

    def comprobar(nombre: str, ok: bool, detalle: str = "") -> None:
        comprobaciones.append({"comprobacion": nombre, "ok": bool(ok), "detalle": detalle})
        print(f"[{'OK ' if ok else 'FALLA'}] {nombre}{' — ' + detalle if detalle else ''}")

    async with async_playwright() as p:
        nav = await p.chromium.launch(args=["--use-gl=swiftshader", "--enable-unsafe-swiftshader",
                                            "--ignore-gpu-blocklist"])
        pagina = await nav.new_page(viewport={"width": 1400, "height": 850})
        pagina.on("console", lambda m: consola.append({"tipo": m.type, "texto": m.text[:300]}))
        pagina.on("pageerror", lambda e: consola.append({"tipo": "pageerror", "texto": str(e)[:300]}))
        pagina.on("requestfailed", lambda r: red.append(
            {"url": r.url, "resultado": r.failure or "fallo",
             "categoria": clasificar(r.url, args.mapa_base, r.failure)}))
        pagina.on("response", lambda r: red.append(
            {"url": r.url, "resultado": r.status, "categoria": clasificar(r.url, args.mapa_base)}))

        if args.mapa_base == "bloqueado":
            await pagina.route(f"https://{HOST_TESELAS}/**", lambda r: r.abort("blockedbyclient"))
        elif args.mapa_base == "sin-respuesta":
            async def colgar(route):  # nunca responde
                await asyncio.sleep(3600)
            await pagina.route(f"https://{HOST_TESELAS}/**", colgar)
        elif args.mapa_base == "simulado":
            await pagina.route(f"https://{HOST_TESELAS}/**",
                               lambda r: r.fulfill(status=200, content_type="image/png", body=TESELA_SIMULADA))

        await pagina.goto(args.url)
        await pagina.wait_for_function("window.estadoMapa && window.estadoMapa.listo", timeout=30000)
        espera_base = 9500 if args.mapa_base == "sin-respuesta" else 2500
        await pagina.wait_for_timeout(espera_base)
        e = await pagina.evaluate("window.estadoMapa")
        await pagina.screenshot(path=str(salida / "1_inicio.png"))

        comprobar("Motor del mapa inicializado", e["motor"]["estado"] == "listo", e["motor"]["detalle"])
        esperado_base = {"real": None, "bloqueado": "no_disponible", "sin-respuesta": "sin_respuesta",
                         "simulado": "disponible"}[args.mapa_base]
        aviso_base = await pagina.is_visible("#aviso-mapa-base")
        if esperado_base:
            comprobar("Estado del mapa base", e["base"]["estado"] == esperado_base,
                      f"{e['base']['estado']} (esperado {esperado_base})")
        else:
            comprobar("Estado del mapa base (informativo)", True, e["base"]["estado"])
        comprobar("Aviso visible solo si el mapa base falla",
                  aviso_base == (e["base"]["estado"] in ("no_disponible", "sin_respuesta", "parcial")),
                  f"aviso visible: {aviso_base}")
        fondo = await pagina.evaluate("mapaTerritorial.getLayer('fondo') && mapaTerritorial.getPaintProperty('fondo','background-color')")
        comprobar("Fondo neutro definido bajo el mapa base", bool(fondo), str(fondo))
        loc = e["capas"].get("localidad", {})
        comprobar("Capa de localidades dibujada aunque falte el mapa base",
                  loc.get("estado") == "cargada" and loc.get("n", 0) > 0, f"{loc.get('n')} polígonos")
        dibujadas = await pagina.evaluate(
            "mapaTerritorial.queryRenderedFeatures({layers:['localidad-relleno']}).length")
        comprobar("Polígonos de localidad presentes en el lienzo", dibujadas > 0, f"{dibujadas} fragmentos")

        # Advertencia de datos sintéticos coherente con los datos
        sinteticas = [c["nombre"] for c in e["capas"].values() if c.get("sintetico")]
        banda = await pagina.is_visible("#aviso-sintetico")
        comprobar("Advertencia sintética coincide con los datos", banda == bool(sinteticas),
                  f"capas sintéticas: {sinteticas or 'ninguna'}; franja visible: {banda}")

        # Zoom con los controles
        z0 = await pagina.evaluate("mapaTerritorial.getZoom()")
        await pagina.click(".maplibregl-ctrl-zoom-in")
        await pagina.wait_for_timeout(900)
        z1 = await pagina.evaluate("mapaTerritorial.getZoom()")
        await pagina.click(".maplibregl-ctrl-zoom-out")
        await pagina.wait_for_timeout(900)
        z2 = await pagina.evaluate("mapaTerritorial.getZoom()")
        comprobar("Botones de zoom", z1 > z0 and abs(z2 - z0) < 0.05, f"{z0:.2f} → {z1:.2f} → {z2:.2f}")

        # Buscador → ficha → encuadre
        await pagina.fill("#q", args.buscar)
        await pagina.wait_for_selector("#resultados li[data-nivel]", timeout=10000)
        primero = pagina.locator("#resultados li[data-nivel]").first
        nivel_b, codigo_b = await primero.get_attribute("data-nivel"), await primero.get_attribute("data-codigo")
        await primero.click()
        await pagina.wait_for_selector("#panel-ficha[data-estado='lista']", timeout=20000)
        await pagina.wait_for_timeout(1200)
        codigo_ficha = await pagina.text_content("#ficha-codigo")
        comprobar("Buscador abre la ficha del resultado", codigo_ficha == codigo_b, f"{nivel_b} {codigo_b}")
        resaltada = await pagina.evaluate(
            f"mapaTerritorial.queryRenderedFeatures({{layers:['{nivel_b}-seleccion']}}).length")
        comprobar("Unidad buscada resaltada en el mapa", resaltada > 0)
        await pagina.screenshot(path=str(salida / "2_busqueda.png"))

        # Clic en una manzana: activar la capa, acercar y pulsar sobre un polígono dibujado
        await pagina.check("input[data-capa='manzana']")
        await pagina.evaluate(f"mapaTerritorial.jumpTo({{zoom: Math.max(mapaTerritorial.getZoom(), {args.zoom_manzanas})}})")
        await pagina.wait_for_function(
            "window.estadoMapa.capas.manzana && window.estadoMapa.capas.manzana.estado === 'cargada'", timeout=60000)
        await pagina.wait_for_timeout(1500)
        punto = await pagina.evaluate("""() => {
            const m = mapaTerritorial, c = m.getCanvas();
            const fs = m.queryRenderedFeatures({layers: ['manzana-relleno']});
            for (const f of fs) {
              const anillo = f.geometry.type === 'Polygon' ? f.geometry.coordinates[0] : f.geometry.coordinates[0][0];
              const x = anillo.reduce((s, p) => s + p[0], 0) / anillo.length;
              const y = anillo.reduce((s, p) => s + p[1], 0) / anillo.length;
              const px = m.project([x, y]);
              if (px.x < 40 || px.y < 40 || px.x > c.clientWidth - 60 || px.y > c.clientHeight - 40) continue;
              const bajo = m.queryRenderedFeatures(px, {layers: ['manzana-relleno']})[0];
              if (bajo) return {x: px.x, y: px.y, codigo: bajo.properties.codigo};
            }
            return null; }""")
        if not punto:
            comprobar("Clic sobre una manzana", False, "no hay manzanas dibujadas en la vista")
        else:
            caja = await pagina.locator("#mapa canvas").bounding_box()
            await pagina.mouse.click(caja["x"] + punto["x"], caja["y"] + punto["y"])
            await pagina.wait_for_function(
                f"document.getElementById('ficha-codigo') && document.getElementById('ficha-codigo').textContent === {json.dumps(punto['codigo'])}",
                timeout=20000)
            comprobar("Clic en el mapa selecciona la manzana y actualiza la ficha", True, punto["codigo"])
            await pagina.wait_for_timeout(800)
            resaltada = await pagina.evaluate(
                "mapaTerritorial.queryRenderedFeatures({layers:['manzana-seleccion']}).length")
            comprobar("Manzana seleccionada resaltada", resaltada > 0)
        await pagina.screenshot(path=str(salida / "3_manzana.png"))
        e = await pagina.evaluate("window.estadoMapa")
        banda = await pagina.is_visible("#aviso-sintetico")
        hay_sint = any(c.get("sintetico") for c in e["capas"].values()) or bool(e["seleccion"] and e["seleccion"]["sintetico"])
        comprobar("Advertencia sintética sigue coherente tras navegar", banda == hay_sint,
                  f"franja visible: {banda}; datos sintéticos: {hay_sint}")
        await nav.close()

    # --- clasificación de consola y red ----------------------------------------------------------
    fallidas = [r for r in red if not (isinstance(r["resultado"], int) and r["resultado"] < 400)]
    no_errores = ("mapa_base_bloqueado_a_proposito", "mapa_base_cancelada_por_el_mapa")
    deliberadas = [r for r in fallidas if r["categoria"] == "mapa_base_bloqueado_a_proposito"]
    canceladas = [r for r in fallidas if r["categoria"] == "mapa_base_cancelada_por_el_mapa"]
    reales = [r for r in fallidas if r["categoria"] not in no_errores]
    patron_deliberado = re.compile(r"ERR_BLOCKED_BY_CLIENT|Mapa base (no_disponible|parcial)|mapa base no respondió", re.I)
    errores_consola = [c for c in consola if c["tipo"] in ("error", "pageerror")]
    consola_deliberada = [c for c in errores_consola if patron_deliberado.search(c["texto"])]
    consola_real = [c for c in errores_consola if not patron_deliberado.search(c["texto"])]
    if args.mapa_base == "real":  # sin bloqueo deliberado: todo cuenta como real
        consola_real, consola_deliberada = errores_consola, []
    comprobar("Sin errores reales de red", not reales, f"{len(reales)} reales, {len(deliberadas)} bloqueadas a propósito, "
              f"{len(canceladas)} teselas canceladas por el mapa al moverse")
    comprobar("Sin errores reales de consola", not consola_real,
              "; ".join(c["texto"][:80] for c in consola_real[:3]))

    resumen_red = {}
    for r in red:
        clave = f"{r['categoria']} · {r['resultado']}"
        resumen_red[clave] = resumen_red.get(clave, 0) + 1
    informe = {
        "fecha": datetime.now().isoformat(timespec="seconds"), "url": args.url,
        "modo_mapa_base": args.mapa_base,
        "nota_modo": {"simulado": "Teselas grises generadas localmente; no es cartografía real.",
                      "bloqueado": "Teselas bloqueadas a propósito por la prueba.",
                      "sin-respuesta": "Teselas retenidas a propósito sin respuesta.",
                      "real": "Teselas solicitadas normalmente a OpenStreetMap."}[args.mapa_base],
        "estado_final": e, "comprobaciones": comprobaciones,
        "aprobado": all(c["ok"] for c in comprobaciones),
        "red_resumen": resumen_red,
        "red_errores_reales": reales[:50],
        "consola_errores_reales": consola_real[:50],
        "consola_errores_deliberados": len(consola_deliberada),
        "consola_avisos": [c for c in consola if c["tipo"] == "warning" and "GL Driver" not in c["texto"]][:20],
    }
    (salida / "informe.json").write_text(json.dumps(informe, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nInforme: {salida / 'informe.json'} · {'APROBADO' if informe['aprobado'] else 'CON FALLAS'}")
    return informe


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--url", default="http://localhost:8000/")
    p.add_argument("--mapa-base", choices=["real", "bloqueado", "sin-respuesta", "simulado"], default="real")
    p.add_argument("--buscar", default="sintetico", help="Texto para el buscador (p. ej. CHAPINERO con datos reales).")
    p.add_argument("--zoom-manzanas", type=float, default=16.0)
    p.add_argument("--salida", default="evidencia")
    informe = asyncio.run(verificar(p.parse_args()))
    return 0 if informe["aprobado"] else 1


if __name__ == "__main__":
    sys.exit(main())
