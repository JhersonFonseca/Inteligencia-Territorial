"""Datos SINTÉTICOS para pruebas. No representan Bogotá: son cuadrados inventados dentro de su
extensión, con códigos inventados. Toda carga hecha con ellos se marca sintetico=True."""

from __future__ import annotations


def cuadrado(x0: float, y0: float, lado: float) -> dict:
    return {"type": "Polygon", "coordinates": [[[x0, y0], [x0 + lado, y0], [x0 + lado, y0 + lado],
                                                 [x0, y0 + lado], [x0, y0]]]}


def feat(props: dict, geom: dict | None) -> dict:
    return {"properties": props, "geometry": geom}


# Localidad sintética "99" de 0.02° x 0.02°, con dos sectores y cuatro manzanas.
X0, Y0 = -74.10, 4.60
LOCALIDADES = [feat({"LOCCODIGO": "99", "LOCNOMBRE": "Localidad Sintética"}, cuadrado(X0, Y0, 0.02))]
UPLS = [feat({"CODIGO_UPL": "U99", "NOMBRE": "UPL Sintética"}, cuadrado(X0, Y0, 0.02))]
SECTORES = [
    feat({"SCACODIGO": "S0001", "SCANOMBRE": "SECTOR SINTETICO A"}, cuadrado(X0, Y0, 0.01)),
    feat({"SCACODIGO": "S0002", "SCANOMBRE": "SECTOR SINTETICO B"}, cuadrado(X0 + 0.01, Y0, 0.01)),
]
MANZANAS = [
    feat({"MANCODIGO": "M00000001", "SECCODIGO": "S0001"}, cuadrado(X0 + 0.001, Y0 + 0.001, 0.003)),
    feat({"MANCODIGO": "M00000002", "SECCODIGO": "S0001"}, cuadrado(X0 + 0.005, Y0 + 0.005, 0.003)),
    feat({"MANCODIGO": "M00000003", "SECCODIGO": "S0002"}, cuadrado(X0 + 0.011, Y0 + 0.001, 0.003)),
    feat({"MANCODIGO": "M00000004", "SECCODIGO": "S0002"}, cuadrado(X0 + 0.015, Y0 + 0.005, 0.003)),
]
ESTRATO_2025 = [
    feat({"CODIGO_MANZANA": "M00000001", "ESTRATO": 2}, MANZANAS[0]["geometry"]),
    feat({"CODIGO_MANZANA": "M00000002", "ESTRATO": 3}, MANZANAS[1]["geometry"]),
    feat({"CODIGO_MANZANA": "M00000003", "ESTRATO": 3}, MANZANAS[2]["geometry"]),
    # M00000004 sin estrato en 2025, a propósito.
]
LOTES = [
    feat({"LOTCODIGO": "L001", "MANZCODIGO": "M00000001"}, None),
    feat({"LOTCODIGO": "L002", "MANZCODIGO": "M00000001"}, None),
    feat({"LOTCODIGO": "L003", "MANZCODIGO": "M00000003"}, None),
]
# Predios 2026 (filas del CSV): lote, estrato. L999 no tiene relación con manzana.
PREDIOS_2026 = ([("L001", "2")] * 5 + [("L001", "3")] * 1 + [("L002", "2")] * 4 +
                [("L003", "3")] * 7 + [("L999", "4")] * 2)


def cargar_demo(conn, registro, carpeta) -> list[dict]:
    """Publica todas las capas sintéticas marcadas como tales. Solo para pruebas y demostración."""
    import csv
    import io
    import zipfile
    from .carga import cargar_desde_csv_zip, cargar_features

    f, dom = registro["fuentes"], registro["dominio_estrato"]
    salida = []
    for fid, feats, con_geom in [("mr_localidad", LOCALIDADES, True), ("mr_upl", UPLS, True),
                                 ("mr_sector", SECTORES, True), ("mr_manzana", MANZANAS, True),
                                 ("sdp_estrato_manzana_2025", ESTRATO_2025, True),
                                 ("mr_lote_manzana", LOTES, False)]:
        campos = sorted({k for x in feats for k in x["properties"]})
        r = cargar_features(conn, f[fid], feats, campos, dom, len(feats), [{"origen": "sintetico"}],
                            con_geometria=con_geom, sintetico=True)
        salida.append({"fuente": fid, "estado": r["estado"], "version_id": r["version_id"]})
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=";")
    w.writerow(["ESOCLOTE", "ESOESTRATO"])
    w.writerows(PREDIOS_2026)
    ruta = carpeta / "predios_2026_SINTETICO.csv.zip"
    with zipfile.ZipFile(ruta, "w") as z:
        z.writestr("predios_sintetico.csv", buf.getvalue())
    r = cargar_desde_csv_zip(conn, f["sdp_estrato_predio_2026"], ruta, dom, sintetico=True)
    salida.append({"fuente": "sdp_estrato_predio_2026", "estado": r["estado"], "version_id": r["version_id"]})
    return salida
