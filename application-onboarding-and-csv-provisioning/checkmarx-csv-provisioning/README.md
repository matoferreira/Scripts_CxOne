# Provisionamiento de aplicaciones Checkmarx One desde CSV

Script en Python para automatizar, a partir de un archivo CSV, la creacion de aplicaciones, grupos de usuarios y la asignacion de esos grupos a cada aplicacion en Checkmarx One.

## Que hace el script

Por cada fila del CSV, ejecuta en este orden:

1. **Crea la aplicacion** (o la reutiliza si ya existe con el mismo nombre)
   - Nombre: columna `Application`
   - Descripcion: columna `Description`
   - Tipo: columna `Type`
   - Regla de asignacion de proyectos: columna `Tag`
   - Tags de la aplicacion: columna `Tag`

2. **Crea dos grupos de usuarios** (o los reutiliza si ya existen)
   - Grupo 1: columna `Group 1`
   - Grupo 2: columna `Group 2`

3. **Asigna ambos grupos a la aplicacion** creada en el paso 1

## Requisitos

- Python 3.11 o superior
- Cliente API de Checkmarx One con `client_id` y `client_secret` validos
- Acceso de red a los endpoints IAM y AST del tenant

## Inicio rapido

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# Editar .env con los valores del tenant
python provision_from_csv.py
```

## Configuracion (.env)

Copiar `.env.example` a `.env` y completar:

```env
TENANT_NAME=tu_tenant_name
OAUTH_CLIENT=tu_oauth_client
SECRET_KEY=tu_secret_key
AST_BASE_URL=ast.checkmarx.net
IAM_BASE_URL=iam.checkmarx.net
APP_CSV_PATH=apps_cx.csv
```

### Variables

| Variable | Descripcion |
|----------|-------------|
| `TENANT_NAME` | Nombre del realm/tenant en Checkmarx |
| `OAUTH_CLIENT` | Client ID del cliente API |
| `SECRET_KEY` | Secret del cliente API |
| `AST_BASE_URL` | Host AST (ej. `ast.checkmarx.net` o `us.ast.checkmarx.net`) |
| `IAM_BASE_URL` | Host IAM (ej. `iam.checkmarx.net` o `us.iam.checkmarx.net`) |
| `APP_CSV_PATH` | Ruta al archivo CSV de entrada (opcional, default: `apps_cx.csv`) |

## Formato del CSV

El archivo debe tener una fila de encabezado y una o mas filas de datos.

| Columna | Uso |
|---------|-----|
| Application | Nombre de la aplicacion |
| Description | Descripcion de la aplicacion |
| Tag | Regla para asignar proyectos y tag de la aplicacion |
| Type | Tipo de aplicacion (ej. `Internal`) |
| Group 1 | Nombre del primer grupo a crear/asignar |
| Group 2 | Nombre del segundo grupo a crear/asignar |

Ejemplo (`apps_cx.csv`):

```csv
Application,Description,Tag,Type,Group 1,Group 2
ABCD,ABCD projects,ABCD,Internal,CHMX_DEV_ABCD_PROD,CHMX_LEAD_ABCD_PROD
```

### Reglas de tag

- Si el tag es un valor simple (ej. `ABCD`), se usa la regla `project.tag.key.exists`.
- Si el tag contiene `;` (ej. `key;value`), se usa la regla `project.tag.key-value.exists`.

## Ejecucion

Con el archivo por defecto (`apps_cx.csv`):

```bash
python provision_from_csv.py
```

Con un CSV especifico:

```bash
python provision_from_csv.py --file ruta/al/archivo.csv
```

## Archivos generados

| Archivo | Contenido |
|---------|-----------|
| `token_cache.json` | Token de acceso en cache (generado automaticamente) |
| `bulk_provision_results.json` | Resultado por fila procesada (exito o error) |

Estos archivos estan en `.gitignore` y no deben versionarse.

## Comportamiento idempotente

- Si la aplicacion ya existe, se reutiliza su ID.
- Si un grupo ya existe, se reutiliza su ID.
- Las asignaciones grupo-aplicacion se ejecutan en cada corrida.

## Solucion de problemas

- **401 Unauthorized al obtener token**: verificar `OAUTH_CLIENT`, `SECRET_KEY`, `TENANT_NAME` y hosts IAM/AST en `.env`.
- **CSV no encontrado**: revisar `APP_CSV_PATH` o el parametro `--file`.
- **Columnas faltantes**: el encabezado debe incluir las 6 columnas requeridas (no sensible a mayusculas/minusculas).
- **403 al asignar grupos**: el cliente API puede no tener permisos de access-management en ese tenant.

## Referencias API

- [Applications Service REST API](https://checkmarx.stoplight.io/docs/checkmarx-one-api-reference-guide/szojm2v0j748d-applications-service-rest-api)
- [Create an assignment](https://checkmarx.stoplight.io/docs/checkmarx-one-api-reference-guide/cdqboeoaz0li5-create-an-assignment)
