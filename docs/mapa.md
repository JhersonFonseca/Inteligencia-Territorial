# Comportamiento del mapa

Revisión del 15 de octubre de 2026, hecha a partir de la captura de la versión 0.1 en la que el área
central del mapa aparecía vacía.

## 1. Diagnóstico

Se reprodujo el caso en Chromium sin interfaz, registrando la consola, la red y el estado interno de
MapLibre.

| Pregunta | Resultado |
|---|---|
| ¿Se inicializaba MapLibre? | **Sí.** Mapa cargado, estilo cargado, lienzo de 780 × 764 px, zoom 10,3 centrado en Bogotá. |
| ¿Se cargaban las capas territoriales? | **Sí.** `/api/capas` y `/api/capas/localidad/geojson` respondieron 200. La capa de localidades estaba visible y sus polígonos aparecían en el lienzo. |
| ¿Fallaban las teselas del mapa base? | **Sí, a propósito.** En el entorno de desarrollo `tile.openstreetmap.org` no es accesible, y la prueba bloqueó esas solicitudes. |
| ¿Había errores reales? | **No.** Los 28 errores de consola eran todos del mapa base: 14 `ERR_BLOCKED_BY_CLIENT` del navegador y 14 `Failed to fetch` de MapLibre. |

El área se veía vacía por la interfaz, no por un fallo de carga:

1. Sin mapa base, el lienzo quedaba transparente y no había un fondo propio.
2. Los polígonos tenían un relleno del 1 % y bordes finos. La única unidad de la demostración
   sintética es un cuadrado de unos 2 km: a zoom 10 es un punto pequeño, y al encuadrarlo ocupa toda
   la vista, así que solo se veía un marco.
3. No había ningún aviso del estado del mapa base ni de las capas.

La revisión encontró además dos problemas reales:

- **Dependencia de un CDN.** MapLibre se cargaba desde `unpkg.com`. En una red que no llegara a ese
  CDN, el mapa no arrancaba en absoluto.
- **Bloqueo cuando el mapa base no responde.** Si el proveedor de teselas deja las solicitudes
  colgadas (en vez de rechazarlas), MapLibre no emite el evento `load`. Las capas territoriales se
  agregaban en ese evento, así que nunca aparecían. Lo detectó la prueba `--mapa-base sin-respuesta`.

## 2. Correcciones

| Cambio | Archivo |
|---|---|
| MapLibre 4.7.1 se sirve desde el proyecto (`web/vendor/`, con su licencia BSD-3). La página no carga scripts ni estilos externos. | `web/index.html`, `web/vendor/` |
| Fondo neutro (`#e9ebe6`) bajo el mapa base: sin teselas se ve un fondo gris claro, no un área en blanco. | `web/app.js` |
| Las capas se agregan con `style.load`, sin esperar a `load` ni a `isStyleLoaded()`. Esta última sigue en falso mientras haya teselas pendientes. | `web/app.js` |
| Seguimiento del mapa base con los estados *cargando*, *disponible*, *incompleto*, *no disponible* y *sin respuesta* (este último tras 8 s sin teselas). Si falla, aparece un aviso sobre el mapa. | `web/app.js` |
| MapLibre ya no imprime un error por cada tesela fallida: se resume en un único aviso `[mapa] …` en la consola. | `web/app.js` |
| Panel **Estado del mapa** con el motor, el mapa base, el zoom y cada capa visible. Para cada capa muestra los polígonos, si hace falta acercar, si hay error y si la capa es *SINTÉTICA* o tiene datos oficiales. | `web/app.js`, `web/index.html` |
| Rellenos más visibles. La unidad seleccionada se resalta en rojo y su capa se activa automáticamente. | `web/app.js` |
| Si MapLibre o WebGL fallan, aparece un aviso, y el buscador y las fichas siguen funcionando. | `web/app.js` |
| La franja de datos sintéticos indica qué capas afectan y no se oculta mientras haya datos sintéticos visibles o en la ficha. | `web/app.js` |
| **Los datos sintéticos nunca reemplazan una versión real publicada.** La carga se rechaza sin crear una versión. La demostración se niega por completo si alguna fuente ya tiene datos reales. | `carga.py`, `sinteticos.py` |

## 3. Verificación automática

`herramientas/verificar_mapa.py` abre el mapa en Chromium y aplica 15 comprobaciones:

- **Mapa:** el motor inicializado, el estado del mapa base, el aviso y el fondo, y las localidades
  dibujadas sin mapa base.
- **Datos sintéticos:** que la franja de aviso coincida con los datos.
- **Navegación:**
  - Zoom con los botones.
  - El buscador abre la ficha correcta y resalta la unidad.
  - Un clic sobre una manzana actualiza la ficha y la resalta.
  - La franja sintética sigue coherente después de navegar.
- **Consola y red:** sin errores reales.

Además clasifica cada solicitud de red en una de estas categorías:

- recurso local;
- API;
- mapa base;
- bloqueada a propósito por la prueba;
- simulada;
- cancelada por el mapa: MapLibre cancela teselas que dejan de hacer falta al acercar o mover el
  mapa, y eso no es un error.

### Escenario 1: demostración sintética (ejecutada el 15 de octubre de 2026)

Todos los datos son inventados. La franja roja estuvo visible en todas las capturas, y el informe
lista las capas sintéticas. Evidencia en `docs/evidencia/2026-10-15_demostracion_sintetica/`.

| Mapa base | Resultado | Red | Consola |
|---|---|---|---|
| Bloqueado (como en el entorno de desarrollo) | 15/15 | 14 solicitudes locales/API correctas; 125 teselas bloqueadas a propósito | 0 errores reales; 1 aviso resumen del mapa base |
| Sin respuesta | 15/15 (antes de la corrección: el mapa no arrancaba) | 14 locales/API correctas; 58 teselas canceladas por el mapa | 0 errores reales; 1 aviso "no respondió en 8 s" |
| Simulado (teselas grises generadas localmente; **no es cartografía**) | 15/15 | 14 locales/API correctas; 54 teselas simuladas, 24 canceladas por el mapa | 0 errores reales |

El modo simulado solo comprueba que el estado pase a *disponible* y que el aviso desaparezca. No
demuestra que OpenStreetMap esté accesible.

### Escenario 2: datos geográficos reales (pendiente, desde tu computador)

Requiere acceso a los servicios oficiales y a OpenStreetMap.

1. Carga las fuentes reales (sección *Cargar los datos oficiales* del README). No ejecutes la
   demostración sintética en esa base. Si ya la ejecutaste, las cargas reales reemplazan a las
   sintéticas.
2. Comprueba que `territorial estado` no muestra ninguna fuente *[SINTÉTICA]*.
3. Instala Playwright una sola vez:

   ```powershell
   py -m pip install playwright
   py -m playwright install chromium
   ```

4. Ejecuta la verificación:

   ```powershell
   py herramientas\verificar_mapa.py --url http://localhost:8000/ --mapa-base real --buscar CHAPINERO --salida docs\evidencia\datos_reales
   ```

5. Comprueba en el informe:
   - El estado del mapa base es *disponible*.
   - La franja sintética **no** aparece.
   - Las 15 comprobaciones están aprobadas.
6. Envíame el resultado, o súbelo a la rama si quieres que quede en el repositorio.

Si en ese entorno la franja sintética aparece con datos reales, o no aparece cuando quedó alguna capa
sintética, la comprobación *Advertencia sintética coincide con los datos* falla.

## 4. Límites conocidos

- Las pruebas en el entorno de desarrollo usan WebGL por software (SwiftShader). El rendimiento con
  decenas de miles de manzanas reales se medirá en el escenario 2.
- La capa de manzanas devuelve como máximo 6000 polígonos por vista. Si se alcanza el límite, el
  panel indica "vista recortada: acerca más".
- Al seleccionar con clic, el mapa no se reencuadra (para no mover la vista mientras se explora).
  Al seleccionar con el buscador, sí.
