"""Reglas de la verificación de extremo a extremo (sin navegador ni base de datos)."""
import copy

from territorial import verificacion as ver

REQ = ver.FUENTES_REQUERIDAS


def fuente(fid, habilitada=True, licencia=True, pendientes=()):
    return {"id": fid, "habilitada": habilitada, "licencia_verificada": licencia,
            "licencia": "CC BY 4.0" if licencia else None, "pendientes": list(pendientes),
            "motivos": [] if habilitada else ["Licencia no verificada."], "ultima_version": None}


def proc(fid, origen="servicio_oficial", version=10, sintetico=False):
    return {"fuente": fid, "origen": origen, "oficial": origen in ver.ORIGENES_OFICIALES,
            "sintetico": sintetico, "version_publicada": version, "habilitada": True, "detalle": ""}


F19 = fuente("sdp_estrato_manzana_2019_rest", habilitada=False, licencia=False,
             pendientes=[{"texto": "licencia", "bloqueante": True}])
FUENTES_OK = [fuente(f) for f in REQ] + [F19]
PROC_OK = [proc(f) for f in REQ] + [{"fuente": F19["id"], "origen": "sin_version", "oficial": False,
                                    "habilitada": False, "version_publicada": None}]


def fallas(res):
    return [r["comprobacion"] for r in res if not r["ok"]]


def test_todo_oficial_aprueba():
    assert fallas(ver.comprobar_fuentes("reales", FUENTES_OK, PROC_OK)) == []


def test_demostracion_sintetica_no_aprueba_como_real():
    p = [proc(f, origen="sintetico", sintetico=True) for f in REQ] + PROC_OK[-1:]
    f = fallas(ver.comprobar_fuentes("reales", FUENTES_OK, p))
    assert len(f) == len(REQ) + 1 and "Ninguna versión publicada es sintética" in f


def test_archivo_sin_procedencia_no_aprueba():
    p = copy.deepcopy(PROC_OK)
    p[REQ.index("sdp_estrato_manzana_2025")] = proc("sdp_estrato_manzana_2025", origen="archivo_sin_procedencia")
    assert fallas(ver.comprobar_fuentes("reales", FUENTES_OK, p)) == ["Procedencia oficial: sdp_estrato_manzana_2025"]


def test_fuente_en_cuarentena_no_aprueba_y_explica_la_causa():
    fs = copy.deepcopy(FUENTES_OK)
    fs[0] = dict(fuente(REQ[0], habilitada=False), motivos=["No hay una versión que haya pasado..."],
                 ultima_version={"id": 3, "estado": "cuarentena", "motivo": "campos: Faltan ['LOCCODIGO']"})
    r = next(x for x in ver.comprobar_fuentes("reales", fs, PROC_OK) if x["comprobacion"].endswith(REQ[0]))
    assert not r["ok"] and "cuarentena" in r["detalle"] and "LOCCODIGO" in r["detalle"]


def test_fuente_con_licencia_pendiente_habilitada_no_aprueba_en_ningun_modo():
    fs = copy.deepcopy(FUENTES_OK)
    fs[-1]["habilitada"] = True
    for modo in ("reales", "sinteticos"):
        assert "Fuentes con licencia o validaciones pendientes siguen deshabilitadas" in \
            fallas(ver.comprobar_fuentes(modo, fs, PROC_OK))


def test_interfaz_con_franja_no_aprueba_como_real():
    ui = {"capas": {"localidad": {"nombre": "Localidad", "sintetico": True}}}
    assert len(fallas(ver.comprobar_interfaz("reales", ui, True))) == 2
    assert fallas(ver.comprobar_interfaz("sinteticos", ui, True)) == []
    assert fallas(ver.comprobar_interfaz("sinteticos", {"capas": {}}, False))


def _ficha(codigo="11", version=10, sintetico=False):
    ref = {"id": "mr_localidad", "version_publicada": version, "sintetico": sintetico}
    return {"codigo": codigo, "contiene_datos_sinteticos": sintetico, "fuente_unidad": ref,
            "bloques": [{"id": "estrato_2019", "estado": "no_validada"}]}


def test_ficha_real():
    assert fallas(ver.comprobar_ficha("reales", _ficha(), PROC_OK, "x")) == []


def test_ficha_con_version_distinta_o_codigo_de_demostracion_no_aprueba():
    assert fallas(ver.comprobar_ficha("reales", _ficha(version=9), PROC_OK, "x"))
    assert fallas(ver.comprobar_ficha("reales", _ficha(codigo="99"), PROC_OK, "x"))
    assert fallas(ver.comprobar_ficha("reales", _ficha(sintetico=True), PROC_OK, "x"))


def test_herramienta_rechaza_combinaciones_enganosas():
    import importlib.util
    import types
    spec = importlib.util.spec_from_file_location("vm", ver.__file__.replace("territorial/verificacion.py", "herramientas/verificar_mapa.py"))
    vm = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(vm)
    a = lambda **k: types.SimpleNamespace(**{"datos": "reales", "mapa_base": "real", "salida": "docs/evidencia/datos_reales",
                                              "buscar": "CHAPINERO", **k})
    assert vm.validar_argumentos(a()) is None
    assert vm.validar_argumentos(a(mapa_base="simulado"))
    assert vm.validar_argumentos(a(salida="docs/evidencia/2026_demostracion_sintetica"))
    assert vm.validar_argumentos(a(buscar="sintetico"))
    assert vm.validar_argumentos(a(datos="sinteticos", salida="docs/evidencia/datos_reales"))
