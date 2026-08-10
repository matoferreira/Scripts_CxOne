# Checkmarx One Scripts Repository

Central collection of Python automation scripts for **Checkmarx One (CxOne)**. Each subdirectory is a self-contained tool with its own dependencies, `.env` configuration, and (where applicable) local documentation.

Use this README as the index for the whole repository. For deep dive details of a specific tool, open the README inside that folder.

---

## Repository layout

| Folder | Purpose |
|--------|---------|
| [`application-onboarding-and-csv-provisioning/`](application-onboarding-and-csv-provisioning/) | Onboard apps: create groups, roles, OAuth clients, applications; bulk provision from CSV |
| [`priority-based-scan-recalculation/`](priority-based-scan-recalculation/) | Recalculate scans based on project priority thresholds from an Excel file |
| [`Script scan all tenant/`](Script%20scan%20all%20tenant/) | Bulk launch / rescan projects across an entire tenant |
| [`Name based project tag script/`](Name%20based%20project%20tag%20script/) | Tag projects with the GitHub org extracted from `org/repo` project names |

> Former names: `script BCP` → `application-onboarding-and-csv-provisioning`; `Script FGS` → `priority-based-scan-recalculation`.

---

## Shared concepts

### Regions (US / US2)

Most scripts support US (sometimes called US1) and US2. Host mapping:

| Region | AST API host | IAM host |
|--------|--------------|----------|
| US / US1 | `ast.checkmarx.net` | `iam.checkmarx.net` |
| US2 | `us.ast.checkmarx.net` | `us.iam.checkmarx.net` |

Full URLs usually look like:

- AST: `https://{ast-host}`
- Token: `https://{iam-host}/auth/realms/{tenant}/protocol/openid-connect/token`

### Authentication

Scripts use one of:

1. **OAuth client credentials** — `client_id` + `client_secret`, `grant_type=client_credentials`
2. **API key (refresh token)** — `grant_type=refresh_token`, `client_id=ast-app`

Never commit `.env` files. Each folder has an `.env.example` / `.envexample` template.

### General setup pattern

```bash
cd "<script-folder>"
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # or .envexample where named that way
# edit .env with tenant, region/hosts, and credentials
```

### External API docs

- [Checkmarx One API Reference](https://checkmarx.stoplight.io/docs/checkmarx-one-api-reference-guide/)
- [Authentication](https://checkmarx.stoplight.io/docs/checkmarx-one-api-reference-guide/ybti0d5u0auto-authentication)

---

## 1. Application onboarding and CSV provisioning

**Folder:** [`application-onboarding-and-csv-provisioning/`](application-onboarding-and-csv-provisioning/)  
**Auth:** OAuth client credentials  
**Local docs:** [README.md](application-onboarding-and-csv-provisioning/README.md) (EN), [README-es.md](application-onboarding-and-csv-provisioning/README-es.md) (ES), [checkmarx-csv-provisioning/README.md](application-onboarding-and-csv-provisioning/checkmarx-csv-provisioning/README.md)

### What it does

Automates a common Checkmarx One **application onboarding** flow, plus **bulk CSV provisioning**:

1. Obtain access token  
2. Create or reuse IAM groups (`CHMX_DEV_{COD_APP}_PROD`, `CHMX_LEAD_{COD_APP}_PROD`)  
3. Assign base roles to those groups  
4. Create or reuse an OAuth client for the app and attach it to the LEAD group + `CHMX_PLUGINSCANNER`  
5. Create or reuse a CxOne application with a project-tag rule  
6. Assign groups to the application  

The CSV path does the same style of work in bulk: for each CSV row, create/reuse application + two groups + group-to-application assignments.

### Main scripts

| Script | Role |
|--------|------|
| `run_all_checkmarx_flow.py` | Full single-app onboarding orchestrator |
| `get_token.py` | Token (client credentials) + cache helpers |
| `create_groups.py` | Create/reuse DEV + LEAD groups |
| `assign_roles.py` | Assign `chmx_dev_base_role` / `chmx_lead_base_role` |
| `create_oauth_client.py` | Create IAM OAuth client for `COD_APP` |
| `create_application.py` | Create/reuse application with tag rule |
| `assign_groups_to_application.py` | Link groups to the application |
| `provision_from_csv.py` | Bulk provision from CSV |
| `checkmarx-csv-provisioning/` | Packaged copy of the CSV provisioner |

### Configuration (`.env` / `.envexample`)

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

For CSV provisioning, also:

```env
APP_CSV_PATH=apps_cx.csv
```

### Quick start

```bash
cd application-onboarding-and-csv-provisioning
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .envexample .env
# edit .env

# Single-app full flow
python run_all_checkmarx_flow.py

# Or bulk from CSV
python provision_from_csv.py
# python provision_from_csv.py --file path/to/apps.csv
```

### CSV format (`apps_cx.csv`)

| Column | Use |
|--------|-----|
| Application | Application name |
| Description | Application description |
| Tag | Project assignment rule + application tags |
| Type | e.g. `Internal` |
| Group 1 | First group name |
| Group 2 | Second group name |

Tag rules:

- Simple value (`ABCD`) → `project.tag.key.exists`
- `key;value` → `project.tag.key-value.exists`

### Generated artifacts (gitignored)

- `token_cache.json`
- `group_ids.json`
- `application_info.json`
- `oauth_client_info.json` (includes client secret — sensitive)
- `bulk_provision_results.json`

### Phase 1 vs Phase 2 tenants

- **Phase 1:** direct base-role assignment may return `400` (code 20). Set `CONTINUE_ON_ROLE_ASSIGNMENT_ERROR=true`. Rely on `PLUGIN_SCANNER_GROUP` for `plugin_scanner` inheritance.
- **Phase 2:** direct group base-role assignment usually works; keep strict failure mode with `CONTINUE_ON_ROLE_ASSIGNMENT_ERROR=false`.

---

## 2. Priority-based scan recalculation

**Folder:** [`priority-based-scan-recalculation/`](priority-based-scan-recalculation/)  
**Auth:** API key (refresh token)  
**Local docs:** [userReadme.md](priority-based-scan-recalculation/userReadme.md) (operator, ES), [README.md](priority-based-scan-recalculation/README.md) (agent/developer guide)

### What it does

1. Reads projects + priorities from `pr_codigo.xlsx`  
2. Authenticates to Checkmarx One  
3. For each project: resolve by **exact name** → latest **Completed** scan → compare age to priority threshold  
4. If overdue, `POST /api/scans/recalculate` reusing that scan’s `branch` and `engines`  
5. Writes audit reports (CSV + Excel) under `reports/`

### Priority thresholds (defaults)

| Priority | Recalculate when last completed scan is older than |
|----------|-----------------------------------------------------|
| 1 | 7 days |
| 2 | 15 days |
| 3 | 30 days |

Override with `PRIORITY_1_DAYS`, `PRIORITY_2_DAYS`, `PRIORITY_3_DAYS` in `.env`.

### Configuration (`.env`)

```env
CHECKMARX_TENANT=your-tenant-name
CHECKMARX_BASE_URL=https://us.ast.checkmarx.net
CHECKMARX_AUTH_URL=https://us.iam.checkmarx.net
CHECKMARX_API_KEY=

PRIORITY_1_DAYS=7
PRIORITY_2_DAYS=15
PRIORITY_3_DAYS=30
```

### Excel input (`pr_codigo.xlsx`)

First sheet, headers (case-insensitive):

| Project name | Priority |
|--------------|----------|
| org/my-project | 1 |
| org/other-project | 2 |

Project names must match Checkmarx **exactly**.

### Quick start

```bash
cd priority-based-scan-recalculation
pip install -r requirements.txt
cp .env.example .env
# edit .env and prepare pr_codigo.xlsx

python checkmarx_rescans.py --dry-run
python checkmarx_rescans.py
# python checkmarx_rescans.py --excel path.xlsx --report-dir reports
```

### Decision statuses

| Status | Meaning |
|--------|---------|
| `TRIGGERED` | Recalculate API called successfully |
| `DRY RUN` | Would recalculate; API not called |
| `SKIPPED` | Not found / no scan / under threshold / no engines |
| `ERROR` | HTTP or unexpected exception (row still written) |

### Reports

```
reports/rescans_report_YYYY-MM-DD_HHMMSS.csv
reports/rescans_report_YYYY-MM-DD_HHMMSS.xlsx
```

Excel sheets: **Summary** (run metadata + counts) and **Results** (one row per project).

---

## 3. Bulk scan all tenant projects

**Folder:** [`Script scan all tenant/`](Script%20scan%20all%20tenant/)  
**Auth:** API key **or** OAuth client  
**Config:** `.env.example` with `CX_TENANT`, `CX_REGION`, credentials, concurrency knobs

### What it does

Orchestrates large-scale scanning across a tenant:

- List all projects (paginated)
- Launch scans / SCM-aware scans / rescans
- Produce CSV + JSON reports and optional “manual review” lists
- Support dry-run and concurrency limits

### Main entrypoints

| Script | Role |
|--------|------|
| `scan_all_projects.py` | Launch scans on all (or limited) projects |
| `scan_platform_scm.py` | SCM re-scan with name prefix filters and multi-branch support |
| `rescan_manual.py` | Rescan via `POST /api/scans/rescan` from a prior report / list |
| `test_scan_launch.py` | Smoke-test launches on the first N eligible SCM projects |
| `run_sast_scan_all.sh` / `run_rescan_until_done.sh` | Shell helpers for long-running jobs |
| `cxone/` | Shared client, scanner orchestration, reporting |

### Configuration (`.env`)

```env
CX_TENANT=your-tenant-name
CX_REGION=US2
CX_API_KEY=
CX_CLIENT_ID=
CX_CLIENT_SECRET=
CX_MAX_CONCURRENT=800
CX_SUBMIT_WORKERS=50
CX_SCAN_TAG=bulk-scan
# CX_SCAN_ENGINES=sast
```

Optional host overrides: `CX_IAM_HOST`, `CX_API_HOST`.

### Quick start

```bash
cd "Script scan all tenant"
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# edit .env

python scan_all_projects.py --dry-run
python scan_all_projects.py --limit 10
python scan_platform_scm.py --dry-run
```

Useful flags (see `--help` on each script): `--dry-run`, `--limit`, `--engines`, `--workers`, `--output`, `--manual-output`.

---

## 4. Name-based project org tags

**Folder:** [`Name based project tag script/`](Name%20based%20project%20tag%20script/)  
**Auth:** OAuth client credentials  
**Entrypoint:** `tag_projects_by_org.py`

### What it does

1. Lists **all** projects in the tenant  
2. Parses names shaped like `github-org/repo-name`  
3. Extracts the org (`github-org`) and adds it as a **key-only** project tag: `{ "github-org": "" }`  
4. Skips if the tag already exists, or if no org can be deduced (no `/` in the name)  
5. Writes a CSV plan; in apply mode, updates the CSV with success/failure per project  

**Important:** CxOne `PATCH /api/projects/{id}` overwrites the full `tags` object. The script **merges** existing tags with the new org tag before PATCHing so other tags are preserved.

### Configuration (`.env`)

```env
CX_TENANT=your-tenant-name
CX_REGION=US2
CX_CLIENT_ID=
CX_CLIENT_SECRET=
# Optional:
# CX_IAM_HOST=us.iam.checkmarx.net
# CX_API_HOST=us.ast.checkmarx.net
```

### Quick start

```bash
cd "Name based project tag script"
pip install -r requirements.txt
cp .env.example .env
# edit .env

python tag_projects_by_org.py --dry-run
python tag_projects_by_org.py --apply
# python tag_projects_by_org.py --dry-run --output ./output/custom.csv
```

Default mode is **dry-run** (no writes). Use `--apply` only when ready to mutate tags.

### CSV columns

| Column | Meaning |
|--------|---------|
| `project_id` | CxOne project ID |
| `project_name` | Full project name |
| `existing_tags` | JSON of tags before the run |
| `org_tag` | Org key to add |
| `action` | `add` / `skip_exists` / `skip_no_org` |
| `update_status` | `dry_run` / `updated` / `failed` / `skipped` / `pending` |
| `error` | Failure detail if any |

Output default path: `output/org_tags_YYYYMMDD_HHMMSS.csv`.

---

## Choosing the right script

| Need | Use |
|------|-----|
| Create app + groups + OAuth client for one `COD_APP` | `application-onboarding-and-csv-provisioning` → `run_all_checkmarx_flow.py` |
| Bulk-create many apps/groups from a spreadsheet/CSV | `application-onboarding-and-csv-provisioning` → `provision_from_csv.py` |
| Recalculate scans on a priority schedule from Excel | `priority-based-scan-recalculation` |
| Scan or rescan (almost) everything in a tenant | `Script scan all tenant` |
| Tag projects with GitHub org from `org/repo` names | `Name based project tag script` |

---

## Safety practices

1. Prefer **dry-run** first whenever the script supports it.  
2. Keep secrets only in local `.env` (never commit).  
3. Confirm **region + tenant** before any mutating run.  
4. Keep generated reports/CSVs as audit evidence.  
5. OAuth clients used by these scripts need the right IAM roles / resource access for the APIs they call.

---

## Adding a new script to this repository

1. Create a new descriptive folder name (what the tool does, not a client codename).  
2. Include `requirements.txt`, `.env.example`, and a local README.  
3. Prefer dry-run / apply modes for any write operations.  
4. Add a section to **this** root README with purpose, auth, config, and quick start.  
5. Do not commit `.env`, token caches, secrets, or large generated reports.

---

## License / ownership

Internal Checkmarx One automation tooling. Use only against tenants you are authorized to administer.
