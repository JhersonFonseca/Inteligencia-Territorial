# Inteligencia territorial · Bogotá (v0.1)

Módulo web que carga capas territoriales oficiales de Bogotá, las valida, guarda cada carga como una
versión trazable y las muestra en un mapa con una ficha por unidad territorial (localidad, UPL,
sector catastral o manzana).

Esta es una versión pequeña de extremo a extremo. **No contiene datos reales todavía**: los datos
oficiales se descargan desde tu computador (o desde staging), porque el entorno donde se programó no
tiene acceso a los servidores de Bogotá.

## Qué hace

- Mapa de Bogotá en el navegador (MapLibre servido desde el propio proyecto + mapa base de
  OpenStreetMap). Si el mapa base no está disponible o no responde, las capas se dibujan sobre un
  fondo neutro y un aviso lo explica; el panel *Estado del mapa* muestra el estado de cada capa
  (ver `docs/mapa.md`).
- Selección de localidad, UPL, sector o manzana con un clic o con el buscador.
- Ficha con tres bloques **separados**, cada uno con su fuente, fecha, licencia, método y advertencias:
  1. Estrato asignado por manzana (2025).
  2. Predios residenciales por estrato (2026), agregados a manzana mediante la relación oficial
     lote → manzana (`MANZCODIGO`). El identificador predial (CHIP) no se lee ni se guarda.
  3. Capa histórica de 2019: **no habilitada** (licencia pendiente); la ficha lo dice explícitamente.
- Las distribuciones se muestran como conteos y porcentajes. No se calculan promedios de estrato.
- Registro de fuentes (`territorial/fuentes.yaml`), validaciones y cuarentena:
  - Toda carga crea una versión nueva. Si falla cualquier validación bloqueante, o si ocurre un
    error, queda **en cuarentena** y el mapa sigue mostrando la versión anterior.
  - Nunca se borra una versión publicada.
  - Una fuente solo se consulta desde la API si tiene licencia verificada, no tiene pendientes
    bloqueantes y tiene una versión publicada. Si no, la API responde 409 con los motivos.
- Datos sintéticos solo para pruebas y demostración, siempre marcados y con aviso rojo en pantalla.

El estrato clasifica inmuebles residenciales para el cobro diferencial de servicios públicos. No
mide ingresos, preferencias ni características de las personas, y la aplicación lo advierte en cada
ficha.

## Requisitos en Windows

1. **Docker Desktop** con WSL 2 (https://www.docker.com/products/docker-desktop/). Ábrelo y espera a
   que diga *Engine running*.
2. **Git para Windows** (https://git-scm.com/download/win).
3. Acceso a Internet para descargar las fuentes oficiales.

Todos los comandos siguientes se escriben en **PowerShell**.

## Instalación

```powershell
git clone -b feature/modulo-territorial https://github.com/JhersonFonseca/Inteligencia-Territorial.git
cd Inteligencia-Territorial
Copy-Item .env.example .env
notepad .env        # cambia la contraseña (en las 3 líneas donde aparece) y tu correo de contacto
docker compose up -d --build
docker compose run --rm api territorial migrar
docker compose run --rm api territorial sincronizar
docker compose run --rm api territorial estado
```

Abre http://localhost:8000. Sin datos cargados, el mapa aparece vacío y el panel izquierdo explica
por qué cada capa no está habilitada.

> Si el puerto 5432 u 8000 ya está ocupado en tu equipo, cambia `TERRITORIAL_PUERTO_BD` o
> `TERRITORIAL_PUERTO` en `.env` y vuelve a ejecutar `docker compose up -d`.

## Cargar los datos oficiales

Cada comando descarga, valida y publica (o deja en cuarentena) una fuente. Las capas del Mapa de
Referencia se descargan por páginas desde el servicio ArcGIS de Catastro.

```powershell
docker compose run --rm api territorial cargar mr_localidad
docker compose run --rm api territorial cargar mr_upl
docker compose run --rm api territorial cargar mr_sector
docker compose run --rm api territorial cargar mr_manzana          # decenas de miles de polígonos: tarda
docker compose run --rm api territorial cargar mr_lote_manzana     # tabla lote → manzana, sin geometría: tarda bastante

docker compose run --rm api territorial descargar sdp_estrato_manzana_2025
docker compose run --rm api territorial cargar-archivo sdp_estrato_manzana_2025 /datos/sdp_estrato_manzana_2025.json

docker compose run --rm api territorial descargar sdp_estrato_predio_2026
docker compose run --rm api territorial cargar-archivo sdp_estrato_predio_2026 /datos/sdp_estrato_predio_2026.csv.zip

docker compose run --rm api territorial estado
docker compose run --rm api territorial consistencia
```

Los archivos descargados quedan en la carpeta `datos\` del proyecto (excluida de Git).

Si una carga queda en cuarentena, `territorial estado` muestra el motivo. Lo más probable en la
primera carga es que un nombre de campo no coincida (los campos de los archivos 2025 y 2026 se
tomaron de los servicios REST equivalentes y se confirman al cargar). En ese caso, corrige `campos`
en `territorial/fuentes.yaml`, ejecuta `docker compose up -d --build`, `territorial sincronizar` y
repite la carga.

## Demostración con datos inventados (opcional)

Para ver el mapa y la ficha funcionando sin descargar nada:

```powershell
docker compose run --rm -e TERRITORIAL_PERMITIR_SINTETICOS=1 api territorial demo-sintetica
```

Aparece una "Localidad Sintética" con cuatro manzanas inventadas cerca del centro del mapa, y una
franja roja avisa que los datos no son reales. Al cargar después los datos oficiales, cada fuente
real reemplaza a su versión sintética. Al revés no es posible: si una fuente ya tiene datos reales
publicados, la demostración se niega a cargarse y ningún dato sintético los reemplaza.
Para quitar la demostración sin borrar nada: `territorial retirar-sinteticos --confirmar`.
`territorial procedencia` muestra el origen de cada versión publicada.

## Pruebas

```powershell
# Pruebas unitarias y de base de datos (sin Internet)
docker compose run --rm api pytest -v

# Pruebas de integración con los servicios oficiales (con Internet)
docker compose run --rm api pytest -m red -v -s
```

Las pruebas de base de datos usan solo la base `territorial_pruebas`, que se crea sola la primera vez
que se levanta Docker Compose. Si tu volumen de base de datos es anterior a esta versión, créala una
vez con:

```powershell
docker compose exec db createdb -U territorial territorial_pruebas
docker compose exec db psql -U territorial -d territorial_pruebas -c "CREATE EXTENSION postgis;"
```

### Resultados en el entorno de desarrollo (15 de octubre de 2026)

| Grupo | Resultado | Qué cubre |
|---|---|---|
| Unitarias (sin red ni base) | 36 aprobadas | Reglas de la verificación de extremo a extremo (una demostración sintética, un archivo sin procedencia o una fuente en cuarentena no aprueban como datos reales; una fuente con licencia pendiente no puede estar habilitada); Paginación por `resultOffset` ordenada por OID (OID que no empiezan en 1), tope de 2000 por página, pérdida detectada por conteo, reintentos ante 5xx y fallos de red, sin reintento ante 4xx, rechazo de capas solo visuales, campos inexistentes y capas sin paginación; validaciones; habilitación por licencia; advertencias de antigüedad; lectura del CSV sin conservar el CHIP; SRID del GeoJSON; descarga incompleta |
| Base de datos (PostgreSQL 16 + PostGIS 3.4) | 25 aprobadas | Migración idempotente; procedencia (servicio oficial, descarga verificada, archivo sin procedencia o modificado, sintético); retiro de versiones sintéticas sin borrar ni tocar las reales; los datos sintéticos no reemplazan versiones reales; la página no usa recursos externos; publicación y reemplazo; cuarentena por pérdida, duplicados, estrato fuera de dominio, coordenadas fuera de Bogotá, campo faltante y error inesperado, conservando siempre la versión anterior; corrección de geometrías inválidas; fuente con licencia pendiente que carga pero no se habilita; agregación lote → manzana; fichas por manzana, sector y localidad; informes de consistencia; API completa |
| Integración con servicios oficiales | **No ejecutadas** | El entorno de desarrollo no tiene acceso a los servidores de Bogotá. Se ejecutan en tu computador (lista abajo) |

| Navegador (`herramientas/verificar_mapa.py --datos sinteticos`) | 20/20 en cada uno de los 3 escenarios del mapa base | Procedencia y habilitación de las fuentes, capa no habilitada negada por la API, motor del mapa, estado y aviso del mapa base, capas dibujadas sin mapa base, zoom, buscador, clic sobre una manzana, ficha, resaltado, advertencia sintética y clasificación de errores de consola y red. Solo con datos sintéticos; detalle y evidencia en `docs/mapa.md` |

Docker Compose no se pudo probar en el entorno de desarrollo (no hay Docker allí). Es la primera
validación de la lista.

## Validaciones que debes ejecutar desde tu computador

**Guía paso a paso con datos oficiales: `docs/validacion_local.md`.** Es la condición para pasar a
staging. La lista siguiente resume qué se valida.

Anota el resultado de cada una; con eso se actualiza `fuentes.yaml` y se cierran los pendientes.

1. **Docker Compose arranca:** `docker compose up -d --build`, luego `docker compose ps` (los dos
   servicios en *running*, la base en *healthy*) y http://localhost:8000/api/salud con
   `"estado": "ok"` y una versión de PostGIS.
2. **Pruebas locales:** `docker compose run --rm api pytest -v` termina con 61 aprobadas.
3. **Servicios oficiales:** `docker compose run --rm api pytest -m red -v -s`. Revisa en la salida:
   - Cada capa del Mapa de Referencia permite consultas, tiene los campos registrados y paginación.
   - La capa histórica de 2019 sigue teniendo 45 051 manzanas y la suma por estrato es igual al total.
   - La paginación real no repite llaves.
   - El GeoJSON de 2025 trae los campos `CODIGO_MANZANA` y `ESTRATO`; anota el SRID y las
     longitudes de `CODIGO_MANZANA` que se imprimen.
   - El ZIP de 2026 trae un único CSV con `ESOCLOTE` y `ESOESTRATO`; anota las columnas.
4. **Cargas reales:** ejecuta la sección *Cargar los datos oficiales* y guarda la salida de
   `territorial estado`. Todas, salvo la de 2019, deben quedar *HABILITADA* sin la marca
   *[SINTÉTICA]*.
5. **Consistencia:** `territorial consistencia`. Anota los tres porcentajes:
   - Los códigos de manzana 2025 encontrados en el Mapa de Referencia. Un valor bajo indicaría
     formatos de código distintos (8 frente a 9 caracteres): **no se rellenan códigos
     automáticamente**; se decide contigo.
   - Los lotes 2026 con relación lote–manzana.
   - La coincidencia entre el estrato 2025 de cada manzana y el estrato más frecuente de sus predios
     2026.
6. **Mapa con datos reales:** ejecuta el escenario 2 de `docs/mapa.md` (verificación automática
   con el mapa base real). Luego abre http://localhost:8000, busca una localidad, acerca el mapa hasta
   ver las manzanas (zoom 15) y compara la ficha de dos o tres manzanas con el visor oficial de Mapas
   Bogotá.
7. **Visor l=22935:** en el navegador, abre el mapa de estratificación de Mapas Bogotá con las
   herramientas de desarrollo (F12 → Red) y anota la URL del servicio que consulta. Sirve para
   confirmar a qué servicio corresponde esa capa (sigue pendiente).
8. **Licencia de la capa 2019:** si encuentras una ficha oficial con su licencia, envíamela. Hasta
   entonces sigue bloqueada.

## Staging en Railway (preparado, no desplegado)

Nada se ha configurado ni desplegado en Railway. Cuando lo autorices, el procedimiento propuesto es:

1. Crear en Railway un **proyecto nuevo** solo para staging (`inteligencia-territorial-staging`), sin
   compartir servicios, variables ni volúmenes con otros proyectos.
2. Servicio de base de datos: imagen Docker `postgis/postgis:16-3.4` con un volumen propio. Antes de
   continuar, comprobar con `SELECT postgis_full_version();` que PostGIS está instalado. (Pendiente
   de verificar con tu cuenta: si Railway ofrece otra plantilla de PostGIS, se compara antes de
   elegir.)
3. Servicio API: desde este repositorio, rama `feature/modulo-territorial`. Usa el `Dockerfile` y
   `railway.json` (comprobación de salud en `/api/salud`; el puerto lo asigna Railway en `PORT`).
4. Variables solo del servicio API:
   - `DATABASE_URL`: referencia a la base de staging.
   - `TERRITORIAL_CONTACTO`: tu correo de contacto.
   - `TERRITORIAL_PERMITIR_SINTETICOS=0`.

   No se define `TERRITORIAL_DB_PRUEBAS` en staging: las pruebas destructivas solo corren en una base
   cuyo nombre termina en `_pruebas`.
5. Una vez desplegado: `territorial migrar`, `territorial sincronizar` y las cargas, ejecutadas como
   comandos únicos del servicio API.

## Estructura

```
territorial/
  fuentes.yaml      registro de fuentes (URL, licencia, fecha, campos, advertencias, pendientes)
  sql/              esquema PostGIS (idempotente, sin borrados)
  arcgis.py         conector ArcGIS REST con paginación, reintentos y bitácora
  descargas.py      descarga completa o nada; lectura de GeoJSON y CSV comprimido
  validacion.py     reglas de calidad (bloqueantes y advertencias)
  carga.py          versión → validación → publicación atómica o cuarentena
  fichas.py         estado de fuentes, capas, búsqueda, fichas e informes de consistencia
  api.py            API FastAPI y servidor del mapa
  cli.py            comando `territorial`
  sinteticos.py     datos inventados para pruebas y demostración
  web/              mapa (HTML, JS y CSS sin compilación; MapLibre 4.7.1 en web/vendor)
herramientas/       verificación automática del mapa en un navegador
docs/               comportamiento del mapa y evidencia de las verificaciones
tests/              pruebas unitarias, de base de datos (bd) e integración (red)
docker/initdb/      crea la base de pruebas al iniciar Docker Compose por primera vez
```

## Limitaciones conocidas

- **Campos de 2025 y 2026:** sin confirmar en los archivos descargables; se validan al cargar.
- **Formato de códigos de manzana:** el código del estrato 2025 y el del Mapa de Referencia podrían
  tener formatos distintos. Se mide en el informe de consistencia; no se transforma ningún código.
- **Asignación a unidades:** las manzanas se asignan a localidad y UPL por su punto interior. A
  sector se asignan por el código de sector del Mapa de Referencia.
- **Sectores catastrales:** no equivalen a los barrios que reconoce la comunidad (se advierte en la
  ficha).
- **Mapa base:** depende de las teselas públicas de OpenStreetMap, adecuadas para desarrollo y
  pruebas. Para uso público con tráfico se debe elegir un proveedor de teselas.
- **Capa 2019:** bloqueada hasta verificar su licencia.
- **Visor l=22935 y endpoint del DANE:** siguen pendientes de verificación.
- **Aún no incluido:** autenticación, cruce con datos de redes sociales y carga programada.

## Ramas

- `main`: estable. Solo contiene el inicio del proyecto y no recibe fusiones sin autorización expresa.
- `feature/modulo-territorial`: desarrollo de esta versión.
