# Validación de extremo a extremo con datos oficiales (desde tu computador)

Esta guía es la condición para pasar a staging. Se ejecuta en Windows con PowerShell, desde la
carpeta del proyecto, en la rama `feature/modulo-territorial`.

**Regla:** en esta base no se ejecuta `demo-sintetica`. Si algo falla, se registra el error y su
causa. No se cargan datos sintéticos para "completar" la prueba: el sistema lo impide (una carga
sintética no puede reemplazar una versión real) y la verificación lo detectaría.

## 0. Preparación (una vez)

```powershell
git fetch origin
git checkout feature/modulo-territorial
git pull
git log --oneline -1          # anota el commit que estás validando
Copy-Item .env.example .env   # solo si aún no tienes .env; edita contraseña y correo
docker compose up -d --build
docker compose ps             # db "healthy" y api "running"
py -m pip install playwright
py -m playwright install chromium
New-Item -ItemType Directory -Force docs\evidencia\datos_reales | Out-Null
```

Abre http://localhost:8000/api/salud: debe decir `"estado": "ok"` e incluir la versión de PostGIS.

## 1. Pruebas locales

```powershell
docker compose run --rm api pytest -v | Tee-Object docs\evidencia\datos_reales\00_pruebas_locales.txt
```

Esperado: **61 aprobadas**.

## 2. Esquema y registro

```powershell
docker compose run --rm api territorial migrar
docker compose run --rm api territorial sincronizar
docker compose run --rm api territorial procedencia
```

Si `procedencia` muestra alguna fuente `SINTETICO` (porque antes ejecutaste la demostración en esta
base), retíralas antes de cargar. El retiro no borra nada:

```powershell
docker compose run --rm api territorial retirar-sinteticos --confirmar
```

## 3. Pruebas contra los servicios oficiales (solo lectura)

```powershell
docker compose run --rm api pytest -m red -v -s | Tee-Object docs\evidencia\datos_reales\01_servicios_oficiales.txt
```

Si falla, **no sigas al paso 4 con esa fuente**: el texto indica qué campo, conteo o capa no coincide.

## 4. Carga oficial

```powershell
docker compose run --rm api territorial cargar mr_localidad
docker compose run --rm api territorial cargar mr_upl
docker compose run --rm api territorial cargar mr_sector
docker compose run --rm api territorial cargar mr_manzana
docker compose run --rm api territorial cargar mr_lote_manzana
docker compose run --rm api territorial descargar sdp_estrato_manzana_2025
docker compose run --rm api territorial cargar-archivo sdp_estrato_manzana_2025 /datos/sdp_estrato_manzana_2025.json
docker compose run --rm api territorial descargar sdp_estrato_predio_2026
docker compose run --rm api territorial cargar-archivo sdp_estrato_predio_2026 /datos/sdp_estrato_predio_2026.csv.zip
```

Cada comando imprime el resultado (`publicada` o `cuarentena`) y sus validaciones. `descargar` guarda
junto al archivo una ficha `.procedencia.json` con la URL, la fecha y la huella sha256. Sin esa
ficha, o si el archivo cambió después de descargarse, la carga se marca como **sin procedencia** y no
cuenta como dato oficial.

Después:

```powershell
docker compose run --rm api territorial estado       | Tee-Object docs\evidencia\datos_reales\02_estado.txt
docker compose run --rm api territorial procedencia  | Tee-Object docs\evidencia\datos_reales\03_procedencia.txt
docker compose run --rm api territorial consistencia | Tee-Object docs\evidencia\datos_reales\04_consistencia.txt
```

Esperado:

- **Estado:** las siete fuentes del mapa *HABILITADA* y sin *[SINTÉTICA]*. La capa histórica de
  2019 sigue *NO HABILITADA*, porque su licencia está pendiente.
- **Procedencia:**
  - las capas del Mapa de Referencia salen como `OFICIAL` por "consultas al servicio registrado";
  - los archivos de 2025 y 2026 salen como `OFICIAL` por "Archivo descargado de la URL registrada".

## 5. Verificación del mapa con datos reales

```powershell
py herramientas\verificar_mapa.py --datos reales --mapa-base real --url http://localhost:8000/ --buscar CHAPINERO --salida docs\evidencia\datos_reales\mapa
```

La verificación solo aprueba si se cumple todo lo siguiente:

1. Las siete fuentes del mapa están habilitadas. Su versión publicada procede de un servicio
   oficial (con el conteo del servicio igual a lo recibido) o de una descarga verificada de la URL
   registrada.
2. Ninguna versión publicada es sintética.
3. Las fuentes con licencia o validaciones pendientes siguen deshabilitadas, y la API niega sus
   capas (HTTP 409).
4. El mapa base real de OpenStreetMap está disponible.
5. La interfaz no muestra la franja de datos sintéticos ni capas marcadas como sintéticas.
6. Las fichas abiertas desde el buscador y con un clic sobre una manzana:
   - no contienen datos sintéticos;
   - no usan códigos de la demostración;
   - remiten exactamente a las versiones publicadas de origen oficial.

   El bloque de 2019 aparece como no validado.
7. Responden el zoom, el buscador, la selección y la ficha.
8. No hay errores reales de consola ni de red.

La herramienta se niega a ejecutarse con `--datos reales` en estos casos:

- si el mapa base está bloqueado o simulado;
- si la carpeta de salida es de evidencia sintética;
- si `--buscar` es el texto de la demostración.

## 6. Si algo falla

- **No** ejecutes `demo-sintetica` ni cargues archivos con `--sintetico` en esta base.
- La verificación deja siempre `informe.json` en la carpeta de salida, incluso si se interrumpe
  (por ejemplo, si la API no responde). El informe incluye:
  - cada comprobación con su detalle;
  - la procedencia de cada fuente;
  - los errores reales de consola y de red;
  - el commit validado.
- Una fuente en cuarentena **no reemplaza** la versión anterior. `territorial estado` muestra el
  motivo, por ejemplo un campo que no coincide o un conteo distinto.
- Envíame:
  - la carpeta `docs\evidencia\datos_reales`;
  - la salida del comando que falló.

  Con eso se corrige la causa (por ejemplo, el nombre de un campo en `fuentes.yaml`) y se repite la
  carga de esa fuente.

## 7. Registro de la evidencia

Para que quede en el repositorio, sube la evidencia a la rama de desarrollo (nunca a `main`):

```powershell
git add docs/evidencia/datos_reales
git commit -m "Evidencia de validación con datos oficiales"
git push
```

La evidencia sintética está en `docs/evidencia/2026-10-15_demostracion_sintetica/` y los controles
negativos en `docs/evidencia/controles_negativos/`. No se mezclan con esta carpeta.
