"""Configuración leída de variables de entorno.

Funciona igual en Docker Compose local (TERRITORIAL_DB) y en Railway (DATABASE_URL, PORT).
"""

from __future__ import annotations

import os
from pathlib import Path

# Extensión de Bogotá D.C. en EPSG:4326, con margen. Derivada de la extensión publicada
# por las capas Localidad y Sector catastral del Mapa de Referencia
# (x -74.4508 a -73.9890, y 3.7307 a 4.8368).
EXTENSION_BOGOTA = (-74.50, 3.70, -73.95, 4.87)

# Antigüedad a partir de la cual un dato genera advertencia visible.
ANIOS_ADVERTENCIA_ANTIGUEDAD = 3

# Tamaño de página máximo que el conector pedirá aunque el servicio permita más.
TAMANO_PAGINA_MAXIMO = 2000


def url_bd() -> str:
    """Cadena de conexión a PostgreSQL. Railway entrega DATABASE_URL."""
    url = os.environ.get("TERRITORIAL_DB") or os.environ.get("DATABASE_URL")
    if not url:
        raise RuntimeError(
            "No hay cadena de conexión. Define TERRITORIAL_DB (local) o DATABASE_URL (Railway)."
        )
    return url


def carpeta_datos() -> Path:
    ruta = Path(os.environ.get("TERRITORIAL_DATOS", "datos"))
    ruta.mkdir(parents=True, exist_ok=True)
    return ruta


def agente_http() -> str:
    contacto = os.environ.get("TERRITORIAL_CONTACTO", "sin-contacto")
    return f"inteligencia-territorial/0.1 (contacto: {contacto})"


def permitir_sinteticos() -> bool:
    """Los datos sintéticos solo se cargan si esta variable está activa (pruebas locales)."""
    return os.environ.get("TERRITORIAL_PERMITIR_SINTETICOS") == "1"
