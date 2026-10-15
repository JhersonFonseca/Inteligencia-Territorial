from datetime import date

from territorial import validacion as v
from territorial.registro import advertencias_fecha, cargar_registro, motivos_no_habilitada

from territorial.sinteticos import cuadrado

DOM = cargar_registro()["dominio_estrato"]


def test_conteo():
    assert v.conteo(10, 10).ok
    r = v.conteo(10, 9)
    assert not r.ok and r.bloqueante and "Pérdida" in r.detalle
    assert v.conteo(None, 5).ok and not v.conteo(None, 5).bloqueante


def test_llaves_duplicadas_y_vacias():
    assert v.llaves_unicas(["a", "b"]).ok
    assert not v.llaves_unicas(["a", "a"]).ok
    assert not v.llaves_unicas(["a", None]).ok
    assert not v.llaves_unicas(["a", ""]).ok


def test_dominio_estrato():
    assert v.dominio_estrato([0, 1, 6, 9, "3"], DOM).ok
    r = v.dominio_estrato([1, 8, "x", None], DOM)
    assert not r.ok and r.bloqueante


def test_campos_presentes():
    assert v.campos_presentes(["A"], ["A", "B"]).ok
    assert "Faltan ['C']" in v.campos_presentes(["A", "C"], ["A"]).detalle


def test_geometria_tipo_y_extension():
    ok = cuadrado(-74.1, 4.6, 0.01)
    assert v.geometria_presente_y_tipo([ok]).ok
    assert not v.geometria_presente_y_tipo([ok, None]).ok
    assert not v.geometria_presente_y_tipo([{"type": "Point", "coordinates": [-74.1, 4.6]}]).ok
    assert v.dentro_de_bogota([ok]).ok
    # Coordenadas planas (p. ej. CTM12 sin transformar) quedan fuera de la extensión
    assert not v.dentro_de_bogota([cuadrado(4880000, 2070000, 100)]).ok


def test_licencia_y_fecha_no_bloquean_la_carga():
    r = v.licencia(False, None)
    assert not r.ok and not r.bloqueante
    assert not v.fecha_dato(None).ok and not v.fecha_dato(None).bloqueante
    assert not v.fecha_dato(date(2019, 8, 15), hoy=date(2026, 10, 15)).ok
    assert v.fecha_dato(date(2025, 11, 20), hoy=date(2026, 10, 15)).ok


def test_habilitacion():
    assert motivos_no_habilitada(True, "CC BY 4.0", [], 1) == []
    m = motivos_no_habilitada(False, None, [{"texto": "x", "bloqueante": True}], None)
    assert len(m) == 3
    assert motivos_no_habilitada(True, "CC BY 4.0", [{"texto": "y", "bloqueante": False}], 1) == []


def test_advertencia_de_antiguedad():
    assert advertencias_fecha(date(2019, 8, 15), hoy=date(2026, 10, 15))
    assert advertencias_fecha(date(2026, 7, 22), hoy=date(2026, 10, 15)) == []
    assert advertencias_fecha(None) == ["Fecha del dato desconocida."]


def test_registro_separa_las_tres_fuentes_de_estrato_y_bloquea_2019():
    f = cargar_registro()["fuentes"]
    for fid in ("sdp_estrato_manzana_2025", "sdp_estrato_predio_2026", "sdp_estrato_manzana_2019_rest"):
        assert fid in f
    f19 = f["sdp_estrato_manzana_2019_rest"]
    assert motivos_no_habilitada(f19.licencia_verificada, f19.licencia, f19.pendientes, 1)
    assert "CHIP" not in str(f["sdp_estrato_predio_2026"].campos).upper()
