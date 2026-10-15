"""Línea de comandos: territorial <comando>.

Comandos:
  migrar                       Crea o actualiza el esquema (idempotente, no borra datos).
  sincronizar                  Copia fuentes.yaml a la base de datos.
  estado                       Muestra el estado y la habilitación de cada fuente.
  cargar <fuente>              Descarga y carga una fuente ArcGIS REST (requiere Internet).
  descargar <fuente>           Descarga el archivo oficial de una fuente de tipo descarga.
  cargar-archivo <fuente> <ruta> [--sintetico]
                               Carga un GeoJSON o CSV comprimido ya descargado.
  consistencia                 Informes de consistencia entre fuentes publicadas.
  informe-fuentes              Registro de validación por fuente (JSON): metadatos, última carga,
                               conteos, campos, SRID, validaciones, estado final y procedencia.
  procedencia                  Origen de cada versión publicada (oficial, sin procedencia, sintética).
  retirar-sinteticos --confirmar
                               Deja de publicar las versiones sintéticas sin borrarlas.
  demo-sintetica               Publica datos INVENTADOS para ver el mapa sin Internet
                               (requiere TERRITORIAL_PERMITIR_SINTETICOS=1; se marcan y se avisan).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import httpx

from . import bd, config, fichas
from .carga import cargar_desde_arcgis, cargar_desde_csv_zip, cargar_desde_geojson, retirar_sinteticos
from .descargas import descargar_archivo, escribir_procedencia
from .registro import cargar_registro, sincronizar


def _imprimir(obj) -> None:
    print(json.dumps(obj, ensure_ascii=False, indent=2, default=str))


def _fuente(registro, fuente_id):
    f = registro["fuentes"].get(fuente_id)
    if not f:
        sys.exit(f"Fuente desconocida: {fuente_id}. Disponibles: {', '.join(registro['fuentes'])}")
    return f


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="territorial", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="comando", required=True)
    sub.add_parser("migrar")
    sub.add_parser("sincronizar")
    sub.add_parser("estado")
    c = sub.add_parser("cargar"); c.add_argument("fuente")
    d = sub.add_parser("descargar"); d.add_argument("fuente")
    a = sub.add_parser("cargar-archivo")
    a.add_argument("fuente"); a.add_argument("ruta")
    a.add_argument("--sintetico", action="store_true",
                   help="Marca la versión como sintética (requiere TERRITORIAL_PERMITIR_SINTETICOS=1).")
    a.add_argument("--srid", type=int, default=None,
                   help="Fuerza el SRID de origen del GeoJSON. Por defecto se usa el declarado en el archivo.")
    sub.add_parser("consistencia")
    sub.add_parser("procedencia")
    sub.add_parser("informe-fuentes")
    sub.add_parser("demo-sintetica")
    r = sub.add_parser("retirar-sinteticos")
    r.add_argument("--confirmar", action="store_true", help="Necesario para aplicar el retiro.")
    args = p.parse_args(argv)

    registro = cargar_registro()

    if args.comando == "descargar":
        f = _fuente(registro, args.fuente)
        if not f.tipo.startswith("descarga_"):
            sys.exit(f"{f.id} no es una fuente de descarga; usa 'territorial cargar {f.id}'.")
        extension = ".json" if f.tipo == "descarga_geojson" else ".csv.zip"
        destino = config.carpeta_datos() / f"{f.id}{extension}"
        try:
            r = descargar_archivo(f.url, destino)
        except (httpx.HTTPError, OSError) as e:
            causa = ("el proxy o la red local rechazó la conexión" if isinstance(e, httpx.ProxyError) else
                     f"el servidor respondió {e.response.status_code}" if isinstance(e, httpx.HTTPStatusError) else
                     "no hubo conexión con el servidor" if isinstance(e, httpx.TransportError) else str(e))
            sys.exit(f"No se pudo descargar {f.id} desde {f.url}: {causa} ({type(e).__name__}: {e}). "
                     "No se modificó ningún archivo ni la base de datos.")
        ficha = escribir_procedencia(r, f.id)
        _imprimir({"archivo": str(r.ruta), "bytes": r.bytes, "sha256": r.sha256, "status": r.status,
                   "procedencia": str(ficha)})
        return 0

    conn = bd.conectar()
    try:
        if args.comando == "migrar":
            _imprimir({"aplicados": bd.migrar(conn), "postgis": bd.postgis_version(conn)})
        elif args.comando == "sincronizar":
            _imprimir({"fuentes_sincronizadas": sincronizar(conn, registro)})
        elif args.comando == "estado":
            for f in fichas.estado_fuentes(conn):
                marca = "HABILITADA" if f["habilitada"] else "NO HABILITADA"
                sint = " [SINTÉTICA]" if f["sintetico"] else ""
                print(f"{f['id']:34} {marca}{sint}")
                for m in f["motivos"]:
                    print(f"    - {m}")
                if f["ultima_version"] and f["ultima_version"]["estado"] == "cuarentena":
                    print(f"    ! Última versión {f['ultima_version']['id']} en cuarentena: "
                          f"{f['ultima_version']['motivo']}")
        elif args.comando == "cargar":
            f = _fuente(registro, args.fuente)
            if f.tipo not in ("arcgis_rest", "arcgis_tabla"):
                sys.exit(f"{f.id} se carga desde archivo: 'territorial descargar {f.id}' y luego "
                         f"'territorial cargar-archivo {f.id} <ruta>'.")
            _imprimir(_resumen(cargar_desde_arcgis(conn, f, registro["dominio_estrato"])))
        elif args.comando == "cargar-archivo":
            f = _fuente(registro, args.fuente)
            if args.sintetico and not config.permitir_sinteticos():
                sys.exit("Los datos sintéticos solo se cargan con TERRITORIAL_PERMITIR_SINTETICOS=1.")
            ruta = Path(args.ruta)
            if ruta.suffix.lower() == ".zip":
                r = cargar_desde_csv_zip(conn, f, ruta, registro["dominio_estrato"], sintetico=args.sintetico)
            else:
                r = cargar_desde_geojson(conn, f, ruta, registro["dominio_estrato"],
                                         srid_origen=args.srid, sintetico=args.sintetico)
            _imprimir(_resumen(r))
        elif args.comando == "demo-sintetica":
            if not config.permitir_sinteticos():
                sys.exit("La demostración sintética requiere TERRITORIAL_PERMITIR_SINTETICOS=1.")
            from .sinteticos import cargar_demo
            try:
                _imprimir(cargar_demo(conn, registro, config.carpeta_datos()))
            except RuntimeError as e:
                sys.exit(str(e))
        elif args.comando == "retirar-sinteticos":
            if not args.confirmar:
                sys.exit("Retira de la consulta todas las versiones sintéticas publicadas (no borra nada). "
                         "Repite con --confirmar para aplicarlo.")
            _imprimir(retirar_sinteticos(conn))
        elif args.comando == "informe-fuentes":
            _imprimir(fichas.informe_fuentes(conn, registro))
        elif args.comando == "procedencia":
            for f in fichas.procedencia(conn):
                marca = "OFICIAL" if f["oficial"] else f["origen"].upper()
                hab = "habilitada" if f["habilitada"] else "no habilitada"
                print(f"{f['fuente']:34} {marca:24} {hab:14} versión {f['version_publicada']}")
                print(f"    {f['detalle']}")
        elif args.comando == "consistencia":
            _imprimir(fichas.consistencia(conn))
    finally:
        conn.close()
    return 0


def _resumen(r: dict) -> dict:
    return {k: r.get(k) for k in ("version_id", "estado", "reemplaza", "motivo", "validaciones") if k in r}


if __name__ == "__main__":
    raise SystemExit(main())
