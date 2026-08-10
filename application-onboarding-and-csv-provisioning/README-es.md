# Scripts de Automatizacion para Checkmarx One

Este repositorio contiene scripts en Python para automatizar un flujo comun de onboarding en Checkmarx One:

1. Obtener token de acceso
2. Crear (o reutilizar) grupos
3. Asignar roles base a esos grupos
4. Crear (o reutilizar) un cliente OAuth y agregarlo a `CHMX_PLUGINSCANNER`
5. Crear (o reutilizar) una aplicacion
6. Asignar los grupos a la aplicacion

## Inicio Rapido

Ejecuta todo el flujo con los pasos minimos:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .envexample .env
# edita .env con tus valores reales
python run_all_checkmarx_flow.py
```

## Requisitos

- Python 3.11+
- Un cliente OAuth valido de Checkmarx One (`client_id` y `client_secret`)

## Preparacion del Entorno

1. Crear y activar entorno virtual:

```bash
python3 -m venv .venv
source .venv/bin/activate
```

2. Instalar dependencias:

```bash
pip install -r requirements.txt
```

3. Configurar `.env` (puedes copiar desde `.envexample`):

```env
TENANT_NAME=tu_tenant_name
OAUTH_CLIENT=tu_oauth_client
SECRET_KEY=tu_secret_key
COD_APP=tu_cod_app
AST_BASE_URL=ast.checkmarx.net
IAM_BASE_URL=iam.checkmarx.net
PLUGIN_SCANNER_GROUP=CHMX_PLUGINSCANNER
CONTINUE_ON_ROLE_ASSIGNMENT_ERROR=false
```

## Archivos Generados

- `token_cache.json`: cache del token de acceso/refresh y metadatos
- `group_ids.json`: IDs de los grupos creados/reutilizados
- `application_info.json`: ID y metadatos de la aplicacion creada/reutilizada
- `oauth_client_info.json`: metadatos del cliente OAuth IAM para `COD_APP` (incluye `clientSecret`; sensible, ignorado por git)

Estos archivos estan ignorados por git.

## Resumen de Cada Script

### `get_token.py`

- Solicita refresh token usando `client_credentials`
- Guarda el token en `token_cache.json`
- Incluye funciones `refresh_access_token()` y helper para header Authorization

### `create_groups.py`

- Primero valida grupos existentes (`GET /api/access-management/groups`)
- Crea solo los grupos faltantes en el endpoint IAM realm
- Grupos objetivo:
  - `CHMX_DEV_{COD_APP}_PROD`
  - `CHMX_LEAD_{COD_APP}_PROD`
- Guarda IDs en `group_ids.json`

### `assign_roles.py`

- Lee los IDs de `group_ids.json`
- Asigna roles base:
  - Grupo DEV -> `chmx_dev_base_role`
  - Grupo LEAD -> `chmx_lead_base_role`

### `create_oauth_client.py`

- Crea cliente OAuth en IAM (`clientId` = `COD_APP`)
- Agrega la cuenta de servicio del cliente a:
  - `CHMX_LEAD_{COD_APP}_PROD`
  - `{PLUGIN_SCANNER_GROUP}` (desde `.env`, default `CHMX_PLUGINSCANNER`)
- Hereda `plugin_scanner` desde `PLUGIN_SCANNER_GROUP` (workaround Phase 1)
- Obtiene el secret con `GET /admin/realms/{realm}/clients/{id}/client-secret`
- Guarda metadatos (incluido el secret) en `oauth_client_info.json`

### `create_application.py`

- Lista aplicaciones con paginacion (`offset` + `limit`)
- Verifica si ya existe una aplicacion con nombre `COD_APP`
- Si no existe, la crea con la regla:
  - `project.tag.key.exists` = `COD_APP`
- Guarda datos en `application_info.json`

### `assign_groups_to_application.py`

- Lee IDs de grupos y de la aplicacion
- Crea asignacion grupo-a-aplicacion (`POST /api/access-management/`)
- Vincula ambos grupos creados/reutilizados a la aplicacion

### `run_all_checkmarx_flow.py`

Script unificado que ejecuta todo el flujo en una sola corrida:

1. Obtencion de token
2. Verificacion/creacion de grupos
3. Asignacion de roles base
4. Creacion de cliente OAuth + asignacion a grupo LEAD y `PLUGIN_SCANNER_GROUP`
5. Verificacion/creacion de aplicacion
6. Asignacion de grupos a la aplicacion

## Phase 1 vs Phase 2

### Tenants Phase 1

- La asignacion directa de roles base a grupos/clientes puede devolver `400 internal error` (code 20).
- Usa `CONTINUE_ON_ROLE_ASSIGNMENT_ERROR=true` para continuar el flujo.
- Los clientes OAuth se agregan a `CHMX_LEAD_{COD_APP}_PROD` y al grupo definido en `PLUGIN_SCANNER_GROUP` (default: `CHMX_PLUGINSCANNER`, debe tener `plugin_scanner` en el tenant).
- No dependas de `POST /base-roles/{entityId}` para `plugin_scanner` directo al cliente OAuth en Phase 1.

### Tenants Phase 2

- Los roles base de grupos (`chmx_dev_base_role`, `chmx_lead_base_role`) pueden asignarse directamente.
- Puedes mantener clientes OAuth en `PLUGIN_SCANNER_GROUP` o migrar a asignacion directa segun politica del tenant.
- Configura `CONTINUE_ON_ROLE_ASSIGNMENT_ERROR=false` para fallar de forma estricta.

## Orden Recomendado de Ejecucion

Si ejecutas scripts por separado:

```bash
python get_token.py
python create_groups.py
python assign_roles.py
python create_oauth_client.py
python create_application.py
python assign_groups_to_application.py
```

O ejecutar todo de una vez:

```bash
python run_all_checkmarx_flow.py
```

## Endpoints API Utilizados

- Endpoint de token (`iam.checkmarx.net` para realm token)
- Gestion de grupos (IAM realm groups + AST access-management groups)
- Asignacion de roles base
- Listado/creacion de aplicaciones
- Creacion de asignaciones

## Solucion de Problemas

- **401 Unauthorized**: token expirado o invalido; los scripts reintentan con refresh token o nuevo token cuando aplica.
- **403 Forbidden**: autenticado, pero sin permisos para esa accion en el tenant.
- **400 en asignacion de roles base** (`internal error`, code 20): comun en tenants Phase 1. Configura `CONTINUE_ON_ROLE_ASSIGNMENT_ERROR=true` en `.env` para registrar el error y continuar el flujo.
- **`PLUGIN_SCANNER_GROUP` no encontrado**: crea/provisiona ese grupo en el tenant (o actualiza `.env`) antes del paso de cliente OAuth.
- **IDs faltantes en archivos de salida**: vuelve a correr el paso previo y valida permisos del cliente API.
