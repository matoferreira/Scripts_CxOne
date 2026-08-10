# Checkmarx One Scripts Repository

Central monorepo of Python automation tools for **Checkmarx One (CxOne)**.

This document is written for **both human operators and AI agents**. Agents should treat mutating Checkmarx API calls as side effects: prefer dry-run / limited runs before live changes, never commit secrets, and keep per-tool local READMEs authoritative for deep details.

---

## Agent quick orientation

| Question | Answer |
|----------|--------|
| What is this repo? | Four independent CxOne automation tools under one root |
| Default branch intent | `master` (also mirrored on `main` historically) |
| Language / runtime | Python 3.10+ (onboarding scripts prefer 3.11+) |
| Secrets location | Per-folder `.env` (gitignored). Templates: `.env.example` / `.envexample` |
| Mutating ops? | Yes — scans, recalculates, project tags, IAM groups/apps/OAuth |
| Safe first action | Always `--dry-run` / small `--limit` when available |
| External API docs | [CxOne API Reference](https://checkmarx.stoplight.io/docs/checkmarx-one-api-reference-guide/), [Auth](https://checkmarx.stoplight.io/docs/checkmarx-one-api-reference-guide/ybti0d5u0auto-authentication) |

### Decision tree (which tool?)

```
Need to create app + groups + OAuth for one COD_APP?
  → application-onboarding-and-csv-provisioning / run_all_checkmarx_flow.py

Need to bulk-create many apps/groups from CSV?
  → application-onboarding-and-csv-provisioning / provision_from_csv.py

Need to recalculate scans by priority from Excel?
  → priority-based-scan-recalculation / checkmarx_rescans.py

Need to scan/rescan most/all projects in a tenant?
  → Script scan all tenant / scan_all_projects.py | scan_platform_scm.py | rescan_manual.py

Need to tag projects with GitHub org from org/repo names?
  → Name based project tag script / tag_projects_by_org.py
```

### Global agent invariants (do not break)

1. **Never commit secrets** — `.env`, `token_cache.json`, `oauth_client_info.json`, API keys, client secrets.
2. **Confirm tenant + region** before any write (`US`/`US2` host mapping below).
3. **Prefer dry-run** for any mutating endpoint; only run live when the user explicitly asks.
4. **Batch resilience** — one project/row failure must not abort the whole batch (existing tools already follow this pattern).
5. **No secrets in stdout/reports** — never print tokens or client secrets.
6. **Keep folder tools self-contained** — do not create a shared cross-folder package unless explicitly requested.
7. **Descriptive folder names** — new tools named by what they do, not by client codenames.

### Shared region hosts

| Region | AST API host | IAM host |
|--------|--------------|----------|
| US / US1 | `ast.checkmarx.net` | `iam.checkmarx.net` |
| US2 | `us.ast.checkmarx.net` | `us.iam.checkmarx.net` |

Token URL pattern:

```text
https://{iam-host}/auth/realms/{tenant}/protocol/openid-connect/token
```

Auth modes used in this repo:

1. **OAuth client credentials** — `grant_type=client_credentials` + `client_id` + `client_secret`
2. **API key (refresh token)** — `grant_type=refresh_token`, `client_id=ast-app`, `refresh_token=<API_KEY>`

### General setup (any tool)

```bash
cd "<script-folder>"
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # or .envexample
# edit .env — never commit it
```

### Repository layout

| Folder | Purpose | Auth | Mutates |
|--------|---------|------|---------|
| [`application-onboarding-and-csv-provisioning/`](application-onboarding-and-csv-provisioning/) | App/group/OAuth onboarding + CSV bulk provision | OAuth client | Yes (IAM + Applications) |
| [`priority-based-scan-recalculation/`](priority-based-scan-recalculation/) | Priority-threshold scan recalculation from Excel | API key | Yes (`/scans/recalculate`) |
| [`Script scan all tenant/`](Script%20scan%20all%20tenant/) | Bulk scan / SCM scan / rescan across tenant | API key or OAuth | Yes (scans) |
| [`Name based project tag script/`](Name%20based%20project%20tag%20script/) | Add org key-only tags from `org/repo` names | OAuth client | Yes (`PATCH /projects/{id}`) |

> Former names: `script BCP` → `application-onboarding-and-csv-provisioning`; `Script FGS` → `priority-based-scan-recalculation`.

---

## 1. Application onboarding and CSV provisioning

**Folder:** [`application-onboarding-and-csv-provisioning/`](application-onboarding-and-csv-provisioning/)  
**Local docs:** [README.md](application-onboarding-and-csv-provisioning/README.md) (EN), [README-es.md](application-onboarding-and-csv-provisioning/README-es.md) (ES), [checkmarx-csv-provisioning/README.md](application-onboarding-and-csv-provisioning/checkmarx-csv-provisioning/README.md)

### Purpose

Automate Checkmarx One application onboarding:

1. Get access token  
2. Create/reuse groups `CHMX_DEV_{COD_APP}_PROD`, `CHMX_LEAD_{COD_APP}_PROD`  
3. Assign base roles  
4. Create/reuse OAuth client for `COD_APP`; attach to LEAD + `PLUGIN_SCANNER_GROUP`  
5. Create/reuse application with project-tag rule  
6. Assign groups to application  

CSV mode repeats create/reuse application + two groups + assignments per row.

### Entrypoints

| Script | Role |
|--------|------|
| `run_all_checkmarx_flow.py` | Full single-app orchestrator |
| `get_token.py` | Token + cache helpers |
| `create_groups.py` | Create/reuse DEV + LEAD groups |
| `assign_roles.py` | Assign `chmx_dev_base_role` / `chmx_lead_base_role` |
| `create_oauth_client.py` | Create IAM OAuth client for `COD_APP` |
| `create_application.py` | Create/reuse application + tag rule |
| `assign_groups_to_application.py` | Group → application assignment |
| `provision_from_csv.py` | Bulk provision from CSV |
| `checkmarx-csv-provisioning/` | Packaged CSV provisioner copy |

### Control flow

```
validate .env
  → get_token (client_credentials) → token_cache.json
  → create_groups (idempotent) → group_ids.json
  → assign_roles (may soft-fail on Phase 1)
  → create_oauth_client → oauth_client_info.json (SENSITIVE)
  → create_application (rule: project.tag.key.exists = COD_APP)
  → assign_groups_to_application
```

CSV path:

```
for each CSV row:
  create/reuse application
  create/reuse Group 1 + Group 2
  assign both groups to application
  append result to bulk_provision_results.json
```

### Configuration

```env
TENANT_NAME=your_tenant_name
OAUTH_CLIENT=your_oauth_client
SECRET_KEY=your_secret_key
COD_APP=your_cod_app
AST_BASE_URL=ast.checkmarx.net
IAM_BASE_URL=iam.checkmarx.net
PLUGIN_SCANNER_GROUP=CHMX_PLUGINSCANNER
CONTINUE_ON_ROLE_ASSIGNMENT_ERROR=false
APP_CSV_PATH=apps_cx.csv
```

### CSV format

| Column | Use |
|--------|-----|
| Application | Application name |
| Description | Description |
| Tag | Project rule + application tags |
| Type | e.g. `Internal` |
| Group 1 / Group 2 | Group names to create/assign |

Tag rules: simple `ABCD` → `project.tag.key.exists`; `key;value` → `project.tag.key-value.exists`.

### Quick start

```bash
cd application-onboarding-and-csv-provisioning
pip install -r requirements.txt
cp .envexample .env
python run_all_checkmarx_flow.py
python provision_from_csv.py
# python provision_from_csv.py --file path/to/apps.csv
```

### Agent notes / invariants

- Prefer **idempotent reuse** of existing apps/groups (scripts already check existence).
- Phase 1 tenants often fail direct base-role assignment (`400` code 20). Set `CONTINUE_ON_ROLE_ASSIGNMENT_ERROR=true` and rely on `PLUGIN_SCANNER_GROUP` for `plugin_scanner`.
- `oauth_client_info.json` contains secrets — never commit or paste into chats/logs.
- Assignment step may return `403` in restricted tenants even when earlier steps succeed.

### APIs touched (summary)

- IAM token endpoint  
- IAM realm groups + AST access-management groups  
- Base-role assignment  
- Applications list/create  
- Access-management assignments  
- IAM OAuth client create + client-secret fetch  

---

## 2. Priority-based scan recalculation

**Folder:** [`priority-based-scan-recalculation/`](priority-based-scan-recalculation/)  
**Local docs:** [userReadme.md](priority-based-scan-recalculation/userReadme.md) (operators, ES), [README.md](priority-based-scan-recalculation/README.md) (**primary agent guide for this tool**)

### Purpose

Single-file CLI that recalculates scans when the latest **Completed** scan is older than a priority threshold.

### Entrypoint

```bash
python checkmarx_rescans.py [--excel PATH] [--report-dir PATH] [--dry-run]
```

Exit codes: `0` if no ERROR rows; `1` on fatal startup failure, empty Excel, or any per-project ERROR.

### Control flow

```
main → run
  load_config / load_priority_thresholds
  load_projects_from_excel (Project name, Priority)
  CheckmarxClient.authenticate (refresh_token)
  for each ProjectRow:
    exact name match → latest Completed scan → age vs threshold
    if overdue and not dry-run: POST /api/scans/recalculate
  write_reports → CSV + XLSX (Summary + Results)
```

### Thresholds (defaults)

| Priority | Days |
|----------|------|
| 1 | 7 |
| 2 | 15 |
| 3 | 30 |

Override via `PRIORITY_{N}_DAYS`.

### Configuration

```env
CHECKMARX_TENANT=your-tenant-name
CHECKMARX_BASE_URL=https://us.ast.checkmarx.net
CHECKMARX_AUTH_URL=https://us.iam.checkmarx.net
CHECKMARX_API_KEY=
PRIORITY_1_DAYS=7
PRIORITY_2_DAYS=15
PRIORITY_3_DAYS=30
```

### Statuses

| Status | Meaning |
|--------|---------|
| `TRIGGERED` | Recalculate succeeded |
| `DRY RUN` | Would recalculate; API skipped |
| `SKIPPED` | Not found / no scan / under threshold / no engines |
| `ERROR` | Failure recorded; loop continues |

### Agent invariants (this tool)

1. Exact project name match only — never fuzzy match.  
2. Only **Completed** scans drive age/engines/branch.  
3. Mutating calls must respect `--dry-run`.  
4. Engine whitelist: `sca`, `sast`, `kics`, `containers`, `system`, `apisec`.  
5. Every attempted project yields an `ExecutionResult` row.

### Safe verification

```bash
cd priority-based-scan-recalculation
python checkmarx_rescans.py --dry-run
# confirm Summary/Results counts before live run
```

---

## 3. Bulk scan all tenant projects

**Folder:** [`Script scan all tenant/`](Script%20scan%20all%20tenant/)  
**Package:** `cxone/` (`client.py`, `scanner.py`, `report.py`)

### Purpose

High-volume tenant scanning:

- Paginated project listing  
- Launch scans (including SCM-aware flows)  
- Rescan without re-upload  
- CSV/JSON reports + manual-review exports  
- Concurrency / queue-aware retries  

### Entrypoints

| Script | Role |
|--------|------|
| `scan_all_projects.py` | Scan all (or `--limit`) projects |
| `scan_platform_scm.py` | SCM scans with name prefix include/exclude + multi-branch |
| `rescan_manual.py` | `POST /api/scans/rescan` from prior report/list |
| `test_scan_launch.py` | Smoke-test first N eligible SCM projects |
| `run_sast_scan_all.sh` / `run_rescan_until_done.sh` | Long-running helpers |

### Architecture

```
CLI entrypoint
  → load_settings() / CxOneClient (token refresh, 401/429 retry)
  → ScanOrchestrator
       list_projects (limit/offset pages of 500)
       launch / SCM projectScan / rescan paths
  → write_report / write_manual_review_report (CSV + JSON)
```

### Configuration

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
# CX_IAM_HOST=...
# CX_API_HOST=...
```

`CX_REGION` maps to hosts via `REGION_IAM` / `REGION_API` in `cxone/client.py` (includes EU/ANZ/etc., not only US/US2).

### Quick start

```bash
cd "Script scan all tenant"
pip install -r requirements.txt
cp .env.example .env
python scan_all_projects.py --dry-run
python scan_all_projects.py --limit 10
python scan_platform_scm.py --dry-run
```

Common flags: `--dry-run`, `--limit`, `--engines`, `--workers`, `--output`, `--manual-output`, `--skip-active-check`.

### Agent notes / invariants

- Treat full-tenant runs as **high impact**; always dry-run or `--limit` first unless user requests full live run.  
- Respect queue/backpressure (client retries 429/503; rescan path may wait on Max Queued).  
- Do not delete historical report artifacts unless asked; they are gitignored.  
- Prefer extending `cxone/scanner.py` / `report.py` over duplicating client auth logic.  
- Scan tags are key-only maps like `{ "bulk-scan": "" }`.

### APIs touched (summary)

- `GET /api/projects` (paginated)  
- `GET /api/projects/last-scan`  
- `POST /api/scans`, `POST /api/scans/rescan`  
- SCM repos-manager `projectScan` endpoints  

---

## 4. Name-based project org tags

**Folder:** [`Name based project tag script/`](Name%20based%20project%20tag%20script/)  
**Entrypoint:** `tag_projects_by_org.py` (self-contained; no package split)

### Purpose

1. List all tenant projects  
2. Parse names like `github-org/repo-name`  
3. Add **key-only** tag `{ "github-org": "" }`  
4. Skip if tag exists or org cannot be deduced (no `/`)  
5. Write CSV plan; on `--apply`, PATCH and update CSV statuses  

### Why merge tags

CxOne `PATCH /api/projects/{id}` **overwrites** the entire `tags` object when tags are submitted. The script always merges `existing_tags + {org: ""}` before PATCH.

### Control flow

```
load .env → OAuth client_credentials
GET /api/projects paginated
for each project:
  extract org before first /
  skip_no_org | skip_exists | plan add
write CSV (both modes)
if --apply:
  for each add row: PATCH merged tags → update CSV row status
print summary
```

### CLI

```bash
python tag_projects_by_org.py --dry-run          # default
python tag_projects_by_org.py --apply
python tag_projects_by_org.py --output path.csv
```

Exit codes: `0` success; `1` config/startup error; `2` one or more update failures.

### Configuration

```env
CX_TENANT=your-tenant-name
CX_REGION=US2
CX_CLIENT_ID=
CX_CLIENT_SECRET=
# CX_IAM_HOST=...
# CX_API_HOST=...
```

Only `US` and `US2` are built-in for this script (override hosts via env if needed).

### CSV schema (`CSV_FIELDS`)

| Column | Meaning |
|--------|---------|
| `project_id` | CxOne ID |
| `project_name` | Full name |
| `existing_tags` | JSON before run |
| `org_tag` | Org key to add |
| `action` | `add` / `skip_exists` / `skip_no_org` |
| `update_status` | `dry_run` / `updated` / `failed` / `skipped` / `pending` |
| `error` | Failure detail |

Default output: `output/org_tags_YYYYMMDD_HHMMSS.csv`.

### Agent invariants (this tool)

1. Tag format is **key-only**: org name is the key, value is `""`.  
2. Never replace tags without merging existing keys.  
3. Default mode is dry-run; `--apply` only when user asks.  
4. Persist CSV after each apply update (mid-run interrupt safety).  
5. Do not invent orgs for names without `/`.

### Safe verification

```bash
cd "Name based project tag script"
python tag_projects_by_org.py --dry-run
# inspect output CSV counts: add / skip_exists / skip_no_org
# only then:
python tag_projects_by_org.py --apply
```

---

## Cross-tool API cheat sheet

| Capability | Typical endpoint | Used by |
|------------|------------------|---------|
| Auth (OAuth) | `POST .../openid-connect/token` client_credentials | onboarding, org tags, bulk scan (optional) |
| Auth (API key) | same token URL, refresh_token + `ast-app` | priority rescans, bulk scan (optional) |
| List projects | `GET /api/projects?limit&offset` | bulk scan, org tags |
| Get project by name | `GET /api/projects?name=` | priority rescans |
| Update project tags | `PATCH /api/projects/{id}` body `{ "tags": {...} }` | org tags |
| List/create apps | `/api/applications` | onboarding / CSV |
| Recalculate scan | `POST /api/scans/recalculate` | priority rescans |
| Launch / rescan | `POST /api/scans`, `POST /api/scans/rescan` | bulk scan |
| SCM project scan | repos-manager `.../projectScan` | bulk scan |

---

## How agents should extend this repo

### Change thresholds / config only

Prefer `.env` — no code change (especially priority days, region, concurrency).

### Add a CLI flag

1. Extend `argparse` in the tool’s entrypoint.  
2. Thread through orchestrator / process functions.  
3. Record the flag in reports/Summary when it affects decisions.  
4. Document in this root README + the tool’s local README.

### Add a new Checkmarx API call

1. Add a client method (reuse auth/session).  
2. Gate mutating calls behind dry-run when the tool has that mode.  
3. Map failures to row-level ERROR/failed status; do not crash the batch.  
4. Include response body snippet in error fields (no secrets).

### Add a new tool folder

1. Descriptive kebab/space name by capability.  
2. `requirements.txt`, `.env.example`, local README (operator + agent notes).  
3. Dry-run for writes.  
4. Update **this** root README (layout table + full section + decision tree).  
5. Ensure root `.gitignore` covers secrets/outputs.

### Split a growing single-file tool

Suggested cut when a file exceeds ~800–1000 lines:

- `client.py` — auth + HTTP  
- `domain.py` / orchestrator — decisions  
- `report.py` — CSV/XLSX writers  
- Keep the public CLI filename stable  

---

## Troubleshooting (shared)

| Symptom | Likely cause |
|---------|----------------|
| `Realm does not exist` | Wrong tenant name |
| Auth 401/400 | Bad/expired credentials or wrong IAM host/region |
| 403 Forbidden | Authenticated but missing permissions / resource access |
| Project not found | Name mismatch (exact match required in priority tool) |
| Tags wiped / unexpected tags | PATCH without merging existing tags (org-tag script merges; keep that) |
| Queue / Max Queued errors | Tenant scan concurrency limits — retry/backoff or lower workers |
| Phase 1 role `400` code 20 | Expected on some tenants — use `CONTINUE_ON_ROLE_ASSIGNMENT_ERROR=true` |

---

## Safety practices (operators + agents)

1. Dry-run first.  
2. Secrets only in local `.env`.  
3. Confirm region + tenant.  
4. Keep reports/CSVs as audit evidence.  
5. Required IAM roles / resource-level access must match the APIs called.  
6. Do not run full-tenant mutating jobs without explicit user approval.

---

## Git / contribution notes

- Root `.gitignore` excludes `.env`, venvs, token caches, OAuth secrets, reports, scan artifacts, zips.  
- Do not reintroduce nested `.git` folders inside tool directories.  
- Prefer small, tool-scoped commits with clear why-focused messages.

---

## License / ownership

Internal Checkmarx One automation tooling. Use only against tenants you are authorized to administer.
