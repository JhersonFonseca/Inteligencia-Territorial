# Validación de extremo a extremo con datos oficiales (desde tu computador)

Esta guía es la condición para pasar a staging. Se ejecuta en Windows desde la carpeta del proyecto,
en la rama `feature/modulo-territorial`.

**Regla:** en esta base no se ejecuta `demo-sintetica`. Si algo falla, se registra el error y su
causa. No se cargan datos sintéticos para "completar" la prueba: el sistema impide que una carga
sintética reemplace una versión real, y la verificación en modo real lo detectaría.

## 0. Preparación (una vez)

Usa **PowerShell 7** (`pwsh`): guarda los archivos en UTF-8. Windows PowerShell 5.1 los guarda en
UTF-16 y puede alterar los acentos. Al abrir la consola, ejecuta:

```powershell
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new()
```

Luego, desde la carpeta del proyecto:

```powershell
git fetch origin
git checkout feature/modulo-territorial
git pull
git log --oneline -1          # anota el commit que estás validando
```

**Configura `.env`:**
- Si no existe, cópialo: `Copy-Item .env.example .env`. Luego cambia la contraseña en las tres líneas
  donde aparece y pon tu correo de contacto.
- Si tu `.env` es anterior, compáralo con `.env.example`. Deben estar `TERRITORIAL_DB_PRUEBAS` (sin
  ella, las 25 pruebas de base de datos se omiten), `TERRITORIAL_PUERTO_BD` y las dos variables de
  proxy.
- Si tu red exige proxy para salir a Internet, pon su dirección en `TERRITORIAL_HTTPS_PROXY` y
  `TERRITORIAL_HTTP_PROXY`. Los contenedores no heredan el proxy de Windows.

Levanta los servicios e instala Playwright:

```powershell
docker compose up -d --build
docker compose ps             # db "healthy" y api "running"
py -m pip install playwright
py -m playwright install chromium   # si tu red lo bloquea, más adelante usa --navegador msedge
New-Item -ItemType Directory -Force docs\evidencia\datos_reales | Out-Null
```

Abre http://localhost:8000/api/salud: debe decir `"estado": "ok"` e incluir la versión de PostGIS.

Si tu volumen de base de datos es anterior a la base de pruebas, créala una vez:

```powershell
docker compose exec db createdb -U territorial territorial_pruebas
docker compose exec db psql -U territorial -d territorial_pruebas -c "CREATE EXTENSION postgis;"
```

Las salidas se guardan con `-T` (sin terminal interactiva) para que el archivo no tenga caracteres de
control.

## 1. Pruebas locales

```powershell
docker compose run --rm -T api pytest -v | Tee-Object docs\evidencia\datos_reales\00_pruebas_locales.txt
```

Esperado: **64 aprobadas**. Si aparecen omitidas (*skipped*), falta `TERRITORIAL_DB_PRUEBAS` en `.env`.

## 2. Esquema y registro

```powershell
docker compose run --rm -T api territorial migrar
docker compose run --rm -T api territorial sincronizar
docker compose run --rm -T api territorial procedencia
```

Si `procedencia` muestra alguna fuente `SINTETICO` (porque antes ejecutaste la demostración en esta
base), retírala antes de cargar. El retiro no borra nada:

```powershell
docker compose run --rm -T api territorial retirar-sinteticos --confirmar
```

## 3. Pruebas contra los servicios oficiales (solo lectura)

```powershell
docker compose run --rm -T api pytest -m red -v -s | Tee-Object docs\evidencia\datos_reales\01_servicios_oficiales.txt
```

Comprueban:

- **Capas del Mapa de Referencia:** que permiten consultas, tienen los campos registrados y aceptan
  paginación, y cuántos registros tienen.
- **Capa histórica de 2019:** que siguen siendo 45 051 manzanas y que la suma por estrato es igual al
  total.
- **Paginación real:** que no repite llaves.
- **Archivo de 2025:** sus campos, su sistema de coordenadas y la longitud de los códigos de manzana.
- **ZIP de 2026:** que trae un único CSV, sus columnas y el número de filas.

Errores de conexión frecuentes:

| Mensaje | Causa probable | Qué hacer |
|---|---|---|
| `ProxyError` o `ConnectError`, aunque el navegador sí abre la página | La red exige proxy | Configura las variables de proxy en `.env` y ejecuta `docker compose up -d` |
| `CERTIFICATE_VERIFY_FAILED` | La red corporativa inspecciona el tráfico cifrado | Pide a sistemas el certificado raíz. No desactives la verificación |
| `HTTP 403` o `404` del servidor oficial | Cambió el servicio o hay una restricción de acceso | Guarda la salida y envíamela |

Si una prueba falla, **no cargues esa fuente** en el paso 4 hasta resolver la causa.

## 4. Carga oficial

```powershell
docker compose run --rm -T api territorial cargar mr_localidad
docker compose run --rm -T api territorial cargar mr_upl
docker compose run --rm -T api territorial cargar mr_sector
docker compose run --rm -T api territorial cargar mr_manzana
docker compose run --rm -T api territorial cargar mr_lote_manzana
docker compose run --rm -T api territorial descargar sdp_estrato_manzana_2025
docker compose run --rm -T api territorial cargar-archivo sdp_estrato_manzana_2025 /datos/sdp_estrato_manzana_2025.json
docker compose run --rm -T api territorial descargar sdp_estrato_predio_2026
docker compose run --rm -T api territorial cargar-archivo sdp_estrato_predio_2026 /datos/sdp_estrato_predio_2026.csv.zip
```

Cada comando imprime si la versión quedó `publicada` o en `cuarentena`, con sus validaciones. Dos
cargas son lentas porque se descargan por páginas de 1000 registros: `mr_manzana` y, sobre todo,
`mr_lote_manzana`. `descargar` guarda junto al archivo una ficha `.procedencia.json` con la URL, la
fecha y la huella sha256. Si la descarga falla, informa la causa y no modifica nada.

Después:

```powershell
docker compose run --rm -T api territorial estado          | Tee-Object docs\evidencia\datos_reales\02_estado.txt
docker compose run --rm -T api territorial procedencia     | Tee-Object docs\evidencia\datos_reales\03_procedencia.txt
docker compose run --rm -T api territorial informe-fuentes | Tee-Object docs\evidencia\datos_reales\04_informe_fuentes.json
docker compose run --rm -T api territorial consistencia    | Tee-Object docs\evidencia\datos_reales\05_consistencia.json
```

`informe-fuentes` arma el registro que pide la validación para cada fuente:

- nombre, entidad, URL, licencia y fecha del dato;
- fecha y hora de la consulta;
- registros esperados y recibidos;
- campos disponibles y claves registradas;
- sistema de coordenadas;
- resultado de cada validación (conteo, llaves únicas, dominio, geometría, extensión, licencia,
  fecha, procedencia);
- estado final (*publicada*, *en cuarentena* o *deshabilitada*) y procedencia.

Esperado:

- **Estado:** las siete fuentes del mapa *HABILITADA* y sin *[SINTÉTICA]*. La capa de 2019 sigue
  *NO HABILITADA* porque su licencia está pendiente.
- **Procedencia:** las capas del Mapa de Referencia `OFICIAL` por consulta al servicio, y los
  archivos de 2025 y 2026 `OFICIAL` por descarga verificada.

## 5. Verificación del mapa con datos reales

```powershell
py herramientas\verificar_mapa.py --datos reales --mapa-base real --url http://localhost:8000/ --buscar CHAPINERO --salida docs\evidencia\datos_reales\mapa
```

Si no se pudo instalar Chromium, añade `--navegador msedge`.

La verificación solo aprueba si se cumple todo lo siguiente:

1. Las siete fuentes del mapa están habilitadas y proceden de un servicio oficial (con el conteo del
   servicio igual a lo recibido) o de una descarga verificada de la URL registrada.
2. Ninguna versión publicada es sintética.
3. Las fuentes con pendientes siguen deshabilitadas, y la API niega sus capas (HTTP 409).
4. El mapa base real está disponible.
5. Las localidades se dibujan, y el buscador encuentra la unidad buscada.
6. No aparece la franja de datos sintéticos.
7. Las fichas abiertas desde el buscador y con un clic sobre una manzana:
   - remiten a las versiones oficiales publicadas;
   - no usan códigos de la demostración;
   - muestran el bloque de 2019 como no validado.
8. Responden el zoom, el buscador, la selección y la ficha.
9. No hay errores reales de consola ni de red.

Para revisar los demás niveles a mano, abre http://localhost:8000:

- selecciona una UPL y un sector catastral desde el buscador o activando su capa;
- comprueba en cada ficha la fuente, la fecha, la licencia y el método de cada bloque.

Guarda capturas en `docs\evidencia\datos_reales\mapa\`.

## 6. Si algo falla

- **No** ejecutes `demo-sintetica` ni cargues archivos con `--sintetico` en esta base.
- La verificación deja siempre `informe.json`, incluso si se interrumpe. El informe incluye:
  - las comprobaciones hechas hasta ese momento;
  - la causa;
  - la procedencia de cada fuente;
  - los errores reales de consola y de red;
  - el commit validado.
- Una fuente en cuarentena **no reemplaza** la versión anterior. `territorial estado` e
  `informe-fuentes` muestran el motivo.
- Envíame la carpeta `docs\evidencia\datos_reales` y la salida del comando que falló.

## 7. Registro de la evidencia

Sube la evidencia a la rama de desarrollo, nunca a `main`:

```powershell
git add docs/evidencia/datos_reales
git commit -m "Evidencia de validación con datos oficiales"
git push
```

Otras evidencias, en carpetas separadas:

- **Demostración sintética:** `docs/evidencia/2026-10-15_demostracion_sintetica/`.
- **Controles negativos:** `docs/evidencia/controles_negativos/`.
- **Pruebas del entorno de desarrollo (sin acceso a los servicios oficiales):**
  `docs/evidencia/entorno_desarrollo/`.
