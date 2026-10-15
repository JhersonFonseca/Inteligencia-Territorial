# Informe de validación integral · versión 0.1

**Fecha:** 15 de octubre de 2026
**Rama:** `feature/modulo-territorial`
**Entorno:** entorno de desarrollo en la nube. No hay acceso a los servicios oficiales de Bogotá, a
OpenStreetMap ni a Docker Hub.

## Dictamen

**No aprobado para staging.**

No existe todavía evidencia de la carga ni de la visualización de datos oficiales. Desde este entorno
los servicios de Bogotá responden con un bloqueo del proxy de salida (HTTP 403 del proxy, no del
servidor oficial). La validación con datos oficiales debe ejecutarse desde tu computador con
`docs/validacion_local.md`.

Lo que no depende de esa conexión se ejecutó y aprobó, y está descrito en este informe:

- pruebas locales;
- PostGIS;
- API;
- inicialización del mapa;
- reglas de procedencia, cuarentena y protección contra datos sintéticos.

Durante esta fase se encontraron y corrigieron cinco errores reproducibles.

## 1. Estado de Git y commit validado

| Elemento | Valor |
|---|---|
| Rama de trabajo | `feature/modulo-territorial` |
| Commit al iniciar la fase | `b6f0a58` (igual en local y en GitHub) |
| `main` | `1b48be5` en local y en GitHub. No se modificó, no se fusionó y no se desplegó nada |
| Commit validado | El que contiene este informe (ver `git log`) |

## 2. Pruebas ejecutadas y resultados

| Prueba | Resultado | Evidencia (`docs/evidencia/entorno_desarrollo/2026-10-15_validacion_v0.1/`) |
|---|---|---|
| Pruebas locales al iniciar la fase | **61/61 aprobadas** | `00_pruebas_locales.txt` |
| Pruebas locales al cerrar la fase | **64/64 aprobadas**: 61 existentes más 3 nuevas por las correcciones | `08_pruebas_finales.txt` |
| Instalación limpia no editable, como en el `Dockerfile` | **64/64 aprobadas**, después de corregir el error 4 | `07_instalacion_limpia_pruebas.txt` |
| PostgreSQL 16 + PostGIS 3.4.2 (base nueva y vacía) | Migración (001 y 002) y registro de las 8 fuentes correctos | `01_base_vacia_estado_procedencia_consistencia.txt` |
| API sobre la base vacía | `/api/salud` 200. Las capas y fichas no habilitadas responden **409** con sus motivos | `04_api_base_vacia.txt` |
| Mapa en modo real sobre la base vacía | No aprueba: 10 fallas y 15 correctas. Detalle abajo | `05_mapa_modo_reales_base_vacia/` |
| `docker compose up` | **No ejecutable aquí**: Docker Hub responde *Forbidden*. `docker compose config` sí valida el archivo | — |
| Pruebas de integración con los servicios oficiales | **No ejecutables aquí**: 403 del proxy de salida | `02_integracion_servicios_oficiales.txt` |

Resultado del mapa en modo real sobre la base vacía:

- **Correcto:**
  - MapLibre se inicializa.
  - El aviso "Mapa base no disponible" se muestra porque OpenStreetMap no es accesible desde aquí.
  - El fondo es neutro y no aparece la franja sintética.
  - El zoom responde.
  - La API niega las 6 capas no habilitadas.
- **Fallas esperadas:**
  - las siete fuentes, por falta de una versión publicada;
  - el mapa base, que no está disponible aquí;
  - no hay localidades dibujadas;
  - el buscador no encuentra "CHAPINERO".

  La herramienta se detuvo en ese punto y registró la causa.

## 3. Fuentes y estado de cada una

No se consultó ningún servicio oficial desde este entorno. Los metadatos de la tabla vienen del
registro (`territorial/fuentes.yaml`), verificado el 15 de octubre de 2026 en las páginas oficiales
durante la fase de diseño. **No** son resultados de carga.

| Fuente | Entidad | Capa o archivo | Licencia | Fecha del dato | Claves registradas | Estado en este entorno |
|---|---|---|---|---|---|---|
| `mr_localidad` | SDP (Mapa de Referencia) | MR/48 | CC BY 4.0 | 2022-08-05 | `LOCCODIGO`, `LOCNOMBRE` | **En cuarentena**: intento de carga bloqueado por el proxy (403) |
| `mr_upl` | SDP | MR/44 | CC BY 4.0 | 2023-02-15 | `CODIGO_UPL`, `NOMBRE` | Deshabilitada (sin carga) |
| `mr_sector` | UAECD | MR/37 | CC BY 4.0 | 2026-07-28 | `SCACODIGO`, `SCANOMBRE` | Deshabilitada (sin carga) |
| `mr_manzana` | UAECD | MR/40 | CC BY 4.0 | 2026-07-28 | `MANCODIGO`, padre `SECCODIGO` | Deshabilitada (sin carga) |
| `mr_lote_manzana` | UAECD | MR/38 (tabla) | CC BY 4.0 | 2026-07-28 | `LOTCODIGO` → `MANZCODIGO` | Deshabilitada (sin carga) |
| **1. Estratificación por manzana 2025** (`sdp_estrato_manzana_2025`) | SDP | `manzanaestratificacion.json` (datos abiertos) | CC BY 4.0 | 2025-11-20 | `CODIGO_MANZANA`, `ESTRATO` (sin confirmar en el archivo) | Deshabilitada. La descarga fue rechazada por el proxy; no se modificó nada |
| **2. Predios por estrato 2026** (`sdp_estrato_predio_2026` + `mr_lote_manzana`) | SDP / UAECD | `esoc.csv.zip` (datos abiertos) | CC BY 4.0 | 2026-07-22 | `ESOCLOTE`, `ESOESTRATO` (sin confirmar en el archivo) | Deshabilitada (sin carga) |
| **3. Capa histórica REST 2019** (`sdp_estrato_manzana_2019_rest`) | SDP | `estratificacion/MapServer/1` | **No verificada** | 2019-08-15 | `CODIGO_MANZANA`, `ESTRATO` | **Deshabilitada por pendiente bloqueante de licencia** |

Las tres fuentes de estratificación siguen separadas en el registro, en la base, en la API y en la
ficha (bloques distintos). La de 2019 no puede habilitarse mientras su licencia esté pendiente: lo
comprueban las pruebas y la verificación en ambos modos.

**Pendiente:** los datos de consulta reales de cada fuente: fecha y hora de consulta, conteo esperado
y recibido, campos disponibles, sistema de coordenadas, resultado de las validaciones y estado final.
Los genera `territorial informe-fuentes` en el paso 4 de `docs/validacion_local.md`.

## 4. Procedencia y consistencia

### Procedencia: comprobada con pruebas automáticas, sin datos oficiales

| Requisito | Prueba | Resultado |
|---|---|---|
| Cada fuente publicada tiene una procedencia verificable | `test_procedencia_de_servicio_arcgis`, `test_procedencia_distingue_…` | Aprobado |
| La huella sha256 coincide con el archivo descargado | `test_procedencia_distingue_…` (caso 2) | Aprobado |
| Un archivo modificado tras su descarga no cuenta como oficial | `test_procedencia_distingue_…` (caso 3: la huella no coincide) | Aprobado |
| Los conteos y las claves cumplen las reglas | `test_fallo_deja_en_cuarentena…` (pérdida, duplicados, dominio, extensión, campo faltante) | Aprobado |
| Una carga fallida queda en cuarentena y conserva la versión anterior | Mismas pruebas, más el intento real de `mr_localidad` en este entorno | Aprobado |
| Los datos sintéticos no sustituyen datos reales | `test_sintetico_no_reemplaza…`, `test_demo_sintetica_se_niega…` | Aprobado |
| Los datos sintéticos no aprueban el modo real | `test_demostracion_sintetica_no_aprueba_como_real` y control negativo con el navegador (29 fallas) | Aprobado |

**Pendiente:** ejecutar `territorial procedencia` sobre los datos oficiales cargados.

### Consistencia: no ejecutable sin datos oficiales

Sobre la base vacía, `territorial consistencia` devuelve `[]`, porque no hay versiones publicadas.

En esta fase se completó el informe para cubrir lo que pide la validación, sin cambiar ninguna regla
de unión. Ahora incluye:

- **Integridad de los códigos de manzana** en el Mapa de Referencia, en 2025 y en la relación
  lote–manzana: registros, vacíos, no numéricos, espacios, distribución de longitudes y llaves
  duplicadas.
- **Registros sin correspondencia, con hasta 10 ejemplos:**
  - códigos de 2025 sin manzana en el Mapa de Referencia;
  - manzanas sin estrato en 2025;
  - manzanas citadas por la relación lote–manzana que no existen en la capa Manzana;
  - lotes de 2026 sin relación, con los predios afectados.
- **Comparación 2025 frente a 2026**, con el número de empates en el estrato más frecuente.
- **Limitaciones metodológicas** escritas en cada informe.

Las llaves duplicadas no llegan a publicarse, porque la regla de llave única es bloqueante. El
informe lo confirma con el conteo de `llaves_duplicadas`.

## 5. Capturas

No hay capturas con datos oficiales. Capturas disponibles:

- `entorno_desarrollo/…/05_mapa_modo_reales_base_vacia/1_inicio.png`: base vacía, mapa base no
  disponible, aviso "No hay capas territoriales habilitadas".
- `2026-10-15_demostracion_sintetica/`: demostración, claramente marcada.
- `controles_negativos/`: modo real sobre datos sintéticos.

## 6. Errores encontrados en esta fase

Todos se reprodujeron y se corrigieron en la rama de desarrollo, y cada uno tiene su prueba o su
evidencia.

| # | Error | Cómo reproducirlo | Corrección |
|---|---|---|---|
| 1 | `territorial descargar` terminaba con un traceback ante un fallo de red y podía dejar un archivo `.parcial` | `territorial descargar sdp_estrato_manzana_2025` sin acceso a la red | Mensaje con la causa, salida sin traceback y eliminación del temporal propio. El archivo anterior no se toca (2 pruebas nuevas) |
| 2 | El informe de consistencia no cubría la integridad de los códigos, los registros sin correspondencia con ejemplos ni los empates | Revisión frente a la sección 6 | Informes adicionales, sin cambiar reglas (prueba ampliada) |
| 3 | La verificación terminaba con traceback si la API no respondía o el buscador no encontraba resultados, y perdía las comprobaciones hechas | Ejecutarla sin API o sobre una base vacía | El informe siempre se guarda, con las comprobaciones hechas y la causa |
| 4 | En la imagen de Docker, una prueba fallaba porque buscaba `herramientas/` junto al paquete instalado, carpeta que la imagen no copia | Instalación no editable y `pytest` | La prueba ubica la carpeta desde `tests/` y el `Dockerfile` la copia. En tu PC habría dado 63/64 |
| 5 | Con la base vacía, el mapa quedaba en blanco sin un aviso en el área del mapa, y la verificación decía "todas con datos oficiales" sin capas cargadas | Base nueva sin cargas | Aviso "No hay capas territoriales habilitadas" y texto corregido |

Ajustes para Windows y redes corporativas:

- Variables de proxy opcionales para los contenedores.
- Opción `--navegador msedge` en la verificación.
- Guía con PowerShell 7 (UTF-8), `-T` en las salidas guardadas y la comprobación de `.env`.
- Nuevo comando `territorial informe-fuentes`, que arma el registro por fuente que pide la sección 3.

## 7. Validaciones pendientes

Todas requieren el computador con acceso a Internet (`docs/validacion_local.md`):

1. Levantar Docker Compose en Windows. Nunca se ha podido probar.
2. Pruebas `-m red` contra los servicios oficiales.
3. Carga de las siete fuentes, `estado`, `procedencia` e `informe-fuentes`.
4. Consistencia sobre datos oficiales. Incluye la duda abierta sobre los códigos de manzana: 8
   frente a 9 caracteres entre `CODIGO_MANZANA` y `MANCODIGO`.
5. Verificación del mapa en modo real, con CHAPINERO, más la revisión manual de UPL y sector, y
   capturas.
6. Pendientes de fases anteriores, que siguen abiertos:
   - la licencia de la capa de 2019;
   - la correspondencia del visor `l=22935`;
   - el endpoint del DANE.

## 8. Condición para cambiar el dictamen

El dictamen pasa a **aprobado** o **aprobado con pendientes** cuando
`docs/evidencia/datos_reales/` contenga:

- las pruebas locales (64/64);
- las pruebas contra los servicios oficiales;
- el estado, la procedencia, el informe por fuente y la consistencia de la carga oficial;
- el `informe.json` aprobado de `verificar_mapa.py --datos reales --mapa-base real`, con sus
  capturas.

Si la consistencia muestra resultados inesperados, se documentará la causa y se propondrá la
corrección antes de modificar cualquier regla.
