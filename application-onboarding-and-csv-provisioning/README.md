# Checkmarx One Automation Scripts

This repository contains Python scripts to automate a common Checkmarx One onboarding flow:

1. Get an access token
2. Create (or reuse) groups
3. Assign base roles to those groups
4. Create (or reuse) an OAuth client and add it to `CHMX_PLUGINSCANNER`
5. Create (or reuse) an application
6. Assign groups to the application

## Quick Start

Run the full flow with the minimum steps:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .envexample .env
# edit .env with your real values
python run_all_checkmarx_flow.py
```

## Requirements

- Python 3.11+
- A valid Checkmarx One OAuth client (`client_id` and `client_secret`)

## Environment Setup

1. Create and activate virtual environment:

```bash
python3 -m venv .venv
source .venv/bin/activate
```

2. Install dependencies:

```bash
pip install -r requirements.txt
```

3. Configure `.env` (you can copy from `.envexample`):

```env
TENANT_NAME=your_tenant_name
OAUTH_CLIENT=your_oauth_client
SECRET_KEY=your_secret_key
COD_APP=your_cod_app
AST_BASE_URL=ast.checkmarx.net
IAM_BASE_URL=iam.checkmarx.net
PLUGIN_SCANNER_GROUP=CHMX_PLUGINSCANNER
CONTINUE_ON_ROLE_ASSIGNMENT_ERROR=false
```

## Generated Files

- `token_cache.json`: cached access/refresh token metadata
- `group_ids.json`: IDs of the generated/reused groups
- `application_info.json`: ID and metadata of the generated/reused application
- `oauth_client_info.json`: IAM OAuth client metadata for `COD_APP` (includes `clientSecret`; sensitive, gitignored)

These files are ignored by git.

## Script-by-Script Overview

### `get_token.py`

- Requests access token using `client_credentials`
- Saves token data to `token_cache.json`
- Includes `refresh_access_token()` and auth header helper methods

### `create_groups.py`

- Checks existing groups first (`GET /api/access-management/groups`)
- Creates missing groups in IAM realm endpoint
- Target groups:
  - `CHMX_DEV_{COD_APP}_PROD`
  - `CHMX_LEAD_{COD_APP}_PROD`
- Saves resulting IDs to `group_ids.json`

### `assign_roles.py`

- Reads group IDs from `group_ids.json`
- Assigns base roles:
  - DEV group -> `chmx_dev_base_role`
  - LEAD group -> `chmx_lead_base_role`

### `create_oauth_client.py`

- Creates IAM OAuth client (`clientId` = `COD_APP`)
- Adds the client service account to:
  - `CHMX_LEAD_{COD_APP}_PROD`
  - `{PLUGIN_SCANNER_GROUP}` (from `.env`, default `CHMX_PLUGINSCANNER`)
- Inherits `plugin_scanner` from `PLUGIN_SCANNER_GROUP` (Phase 1 workaround)
- Retrieves client secret via `GET /admin/realms/{realm}/clients/{id}/client-secret`
- Saves metadata (including secret) in `oauth_client_info.json`

### `create_application.py`

- Lists applications using pagination (`offset` + `limit`)
- Checks if application named `COD_APP` already exists
- If missing, creates it with rule:
  - `project.tag.key.exists` = `COD_APP`
- Saves app metadata to `application_info.json`

### `assign_groups_to_application.py`

- Reads group IDs and application ID
- Creates group-to-application assignment (`POST /api/access-management/`)
- Links both created/reused groups to the application

> Note: In restricted test tenants, this final assignment can fail with `403 forbidden action` even when previous steps succeed.

### `run_all_checkmarx_flow.py`

Single orchestrated script that runs the full flow in one execution:

1. Token retrieval
2. Group check/create
3. Base role assignment
4. OAuth client create + assign to LEAD group and `PLUGIN_SCANNER_GROUP`
5. Application check/create
6. Group-to-application assignment

## Phase 1 vs Phase 2

### Phase 1 tenants

- Direct base-role assignment to groups/clients may return `400 internal error` (code 20).
- Use `CONTINUE_ON_ROLE_ASSIGNMENT_ERROR=true` to continue the flow.
- OAuth clients are added to `CHMX_LEAD_{COD_APP}_PROD` and to the pre-existing group defined by `PLUGIN_SCANNER_GROUP` (default: `CHMX_PLUGINSCANNER`, must already have `plugin_scanner` in the tenant).
- Do **not** rely on direct `POST /base-roles/{entityId}` for `plugin_scanner` on the OAuth client in Phase 1.

### Phase 2 tenants

- Group base roles (`chmx_dev_base_role`, `chmx_lead_base_role`) can be assigned directly.
- You may still keep OAuth clients in `PLUGIN_SCANNER_GROUP`, or switch to direct role assignment if your tenant policy requires it.
- Set `CONTINUE_ON_ROLE_ASSIGNMENT_ERROR=false` for strict failure behavior.

## Recommended Execution Order

If running scripts individually:

```bash
python get_token.py
python create_groups.py
python assign_roles.py
python create_oauth_client.py
python create_application.py
python assign_groups_to_application.py
```

Or run all in one shot:

```bash
python run_all_checkmarx_flow.py
```

## API Endpoints Used

- Token endpoint (`iam.checkmarx.net` realm token)
- Group management (IAM realm groups + AST access-management groups)
- Base role assignment
- Applications list/create
- Assignment create

## Troubleshooting

- **401 Unauthorized**: token expired or invalid; scripts retry using refresh token or new token when applicable.
- **403 Forbidden**: authenticated but missing permissions for that action in the tenant.
- **400 on base-role assignment** (`internal error`, code 20): common in Phase 1 tenants. Set `CONTINUE_ON_ROLE_ASSIGNMENT_ERROR=true` in `.env` to log and continue the flow.
- **`PLUGIN_SCANNER_GROUP` not found**: create/provision that group in the tenant (or update `.env`) before running OAuth client setup.
- **Missing IDs in output files**: re-run prior step and verify API client permissions.
