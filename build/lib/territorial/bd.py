"""Conexión y migraciones."""

from __future__ import annotations

from importlib import resources

import psycopg
from psycopg.rows import dict_row

from . import config


def conectar(url: str | None = None) -> psycopg.Connection:
    return psycopg.connect(url or config.url_bd(), row_factory=dict_row)


def migrar(conn: psycopg.Connection) -> list[str]:
    """Aplica los scripts SQL en orden. Son idempotentes y no borran datos."""
    aplicados = []
    carpeta = resources.files("territorial") / "sql"
    for archivo in sorted(p for p in carpeta.iterdir() if p.name.endswith(".sql")):
        conn.execute(archivo.read_text(encoding="utf-8"))
        aplicados.append(archivo.name)
    conn.commit()
    return aplicados


def postgis_version(conn: psycopg.Connection) -> str:
    return conn.execute("SELECT postgis_full_version() AS v").fetchone()["v"]
