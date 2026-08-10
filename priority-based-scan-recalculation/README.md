# Agent Guide: Checkmarx One Priority-Based Recalculation

This document is for AI agents modifying or extending this repository. It describes purpose, architecture, control flow, and safe extension patterns.

## Purpose

Single-file Python CLI that:

1. Reads projects + priorities from `pr_codigo.xlsx`.
2. Authenticates to Checkmarx One (IAM refresh-token → Bearer).
3. For each project: resolve by exact name → fetch latest **Completed** scan → compare age to priority threshold.
4. If overdue, `POST /api/scans/recalculate` reusing that scan’s `branch` and `engines`.
5. Writes audit reports (CSV + Excel) under `reports/`.

Human operators run it manually (or via scheduler). Agents should treat recalculation as a **side-effecting API call**; prefer `--dry-run` when validating logic.

## Layout

```
.
├── checkmarx_rescans.py   # Entire application (CLI + API client + domain + reports)
├── pr_codigo.xlsx         # Input: Project name | Priority
├── .env / .env.example    # Credentials + PRIORITY_N_DAYS (+ optional paths)
├── requirements.txt       # openpyxl, python-dotenv, requests
├── userReadme.md          # Spanish operator guide (install, config, run, troubleshooting)
└── reports/               # Generated; gitignored
```

Operator-facing documentation (Spanish): see [`userReadme.md`](userReadme.md). This file (`README.md`) is for AI agents.

There is no package split today. New features should stay in `checkmarx_rescans.py` until complexity clearly warrants modules (e.g. `client.py`, `reports.py`, `excel_io.py`).

## Runtime / dependencies

- Python ≥ 3.10 (uses `list[str]`, `X | Y`, dataclasses).
- Install: `pip install -r requirements.txt`
- Secrets live only in `.env` (never commit). Required vars:

| Variable | Role |
|----------|------|
| `CHECKMARX_TENANT` | IAM realm name (exact match) |
| `CHECKMARX_BASE_URL` | AST API base (no trailing slash) |
| `CHECKMARX_AUTH_URL` | IAM base (no trailing slash) |
| `CHECKMARX_API_KEY` | Refresh token / API key |

Optional: `PRIORITY_1_DAYS`, `PRIORITY_2_DAYS`, `PRIORITY_3_DAYS`, `PROJECTS_EXCEL`, `REPORT_DIR`.

## CLI contract

```bash
python checkmarx_rescans.py [--excel PATH] [--report-dir PATH] [--dry-run]
```

Exit codes: `0` success (no ERROR rows); `1` fatal startup failure, empty Excel, or any per-project ERROR.

`--dry-run`: same decision path as production, but skips `recalculate()`; status becomes `DRY RUN` (counted as triggered in console totals).

## Architecture (mental model)

```
main() → argparse
  └─ run()
       ├─ load_config() / load_priority_thresholds()
       ├─ load_projects_from_excel()
       ├─ CheckmarxClient.authenticate()
       └─ for each ProjectRow:
            process_project()  → ExecutionResult
       └─ write_reports() → CSV + XLSX (Summary + Results)
```

### Layers inside one file

| Concern | Symbols | Notes |
|---------|---------|--------|
| Config | `CheckmarxConfig`, `load_config`, `load_priority_thresholds` | Env-driven; fail fast on missing/invalid |
| Input | `ProjectRow`, `load_projects_from_excel` | Header names case-insensitive: `project name`, `priority` |
| API | `CheckmarxClient` | Session + Bearer; path helpers via `_api_url` |
| Domain | `should_recalculate`, `extract_engines_from_scan`, `parse_scan_timestamp`, `process_project` | Pure-ish helpers + orchestration |
| Output | `ExecutionResult`, `REPORT_COLUMNS`, `write_*` | Stable report schema for audit |

### Decision statuses

| Status | Meaning |
|--------|---------|
| `TRIGGERED` | Recalculate API called successfully |
| `DRY RUN` | Would recalculate; API not called |
| `SKIPPED` | Not found / no scan / under threshold / no engines |
| `ERROR` | HTTP or unexpected exception (row still written) |

Per-project failures do **not** abort the loop; fatal errors only in `main`/`run` setup.

## Checkmarx API usage

Auth:

- `POST {AUTH_URL}/auth/realms/{TENANT}/protocol/openid-connect/token`
- Body: `grant_type=refresh_token`, `client_id=ast-app`, `refresh_token=<API_KEY>`
- Headers on subsequent calls: `Accept` / `Content-Type` = `application/json; version=1.0`

Data:

| Method | Path | Usage |
|--------|------|--------|
| GET | `/api/projects?name=&limit=20` | Exact name match among results; >1 exact → `ValueError` |
| GET | `/api/scans?project-id=&statuses=Completed&limit=1&sort=-created_at` | Latest completed scan |
| GET | `/api/scans/{id}` | Fallback if list payload lacks engines |
| POST | `/api/scans/recalculate` | Body: `project_id`, optional `branch`, `engines` |

Engines whitelist: `VALID_ENGINES = {sca, sast, kics, containers, system, apisec}`. Extraction order: `scan.engines` → `metadata.configs[].type` → `statusDetails[].name` (skip `general`).

Timestamps: prefer `updatedAt`, else `createdAt`; UTC-normalized.

## Input Excel contract

First sheet, row 1 headers (exact logical names, case-insensitive):

- `Project name` — must match Checkmarx project name **exactly** (trim whitespace).
- `Priority` — integer in threshold keys (currently `1`, `2`, `3`).

Invalid/empty rows → warn + skip; unsupported priority → warn + skip.

## Priority thresholds

Defaults in `DEFAULT_PRIORITY_THRESHOLDS_DAYS`: `{1: 7, 2: 15, 3: 30}`.

Overridable via `PRIORITY_{N}_DAYS` env vars. Recalculate when `(now - last_scan) > timedelta(days=threshold)` (strictly greater than N days).

`valid_priorities` for Excel = keys of the loaded threshold dict. Adding priority `4` requires both a default (or env) and documentation of the schedule.

## Report schema

`REPORT_COLUMNS` is the canonical column order. `ExecutionResult.as_row()` must stay aligned with it.

Excel report sheets:

- **Summary**: run metadata (tenant, URLs, thresholds, counts).
- **Results**: one row per processed project.

When adding fields: extend `ExecutionResult`, `REPORT_COLUMNS`, and Summary dict in `run()` if the field is run-level.

---

## How to extend (agent playbook)

### 1. Change thresholds only

Prefer `.env` (`PRIORITY_N_DAYS`). No code change. Summary sheet already records values used.

### 2. Add a new priority level (e.g. 4)

1. Add to `DEFAULT_PRIORITY_THRESHOLDS_DAYS`.
2. Document `PRIORITY_4_DAYS` in `.env.example`.
3. Update Summary keys in `run()` (`priority_4_days`).
4. Update this README and any human-facing notes.

Excel rows with priority 4 will load automatically once the key exists in thresholds.

### 3. Add a CLI flag

1. Extend `argparse` in `main()`.
2. Thread the value through `run(...)` → `process_project(...)` if needed.
3. Prefer env fallbacks for path-like options (see `PROJECTS_EXCEL`, `REPORT_DIR`).
4. Include the flag in report Summary when it affects decisions (like `dry_run`).

### 4. Add a new Checkmarx API capability

1. Add a method on `CheckmarxClient` (keep auth/session reuse).
2. Call it from `process_project` (or a new orchestrator helper).
3. Map failures to `ExecutionResult` with `status="ERROR"`; do not crash the batch loop.
4. Use `response.raise_for_status()`; include response body in `error`.
5. Prefer `--dry-run` gating for any mutating endpoint.

### 5. Change skip / trigger rules

Edit `should_recalculate` (age logic) or the branches in `process_project` (not-found, no engines, etc.). Keep reasons human-readable; they appear in reports and stdout.

### 6. Support extra Excel columns

1. Parse optional headers in `load_projects_from_excel`.
2. Extend `ProjectRow` with new fields (defaults for missing columns).
3. Plumb into `process_project` / reports as needed.
4. Remain backward-compatible: existing two-column files must still work.

### 7. Split into modules (when justified)

Suggested cut if the file grows past ~800–1000 lines or gains a second entrypoint:

- `cx_client.py` — `CheckmarxClient`, auth, engines helpers
- `excel_io.py` — load projects
- `reporting.py` — `ExecutionResult`, writers
- `checkmarx_rescans.py` — CLI + `run` / `process_project`

Keep public CLI: `python checkmarx_rescans.py`.

### 8. Add tests (recommended pattern)

No test suite today. If adding one:

- Unit-test pure helpers: `should_recalculate`, `extract_engines_from_scan`, `parse_scan_timestamp`, Excel loading with temp files.
- Mock `requests.Session` for client methods; never hit real Checkmarx in CI.
- Fixture scans: list payload without engines + detail payload with engines.

### 9. Scheduling / automation

Script is already idempotent per run (decides from live last scan). Wrap with cron/CI; always keep reports. Do not log API keys. Prefer dry-run jobs for config validation.

---

## Invariants agents must not break

1. **Exact project name match** — never fuzzy-match or take the first API hit without equality check.
2. **Only Completed scans** drive age and engines/branch.
3. **Mutating calls** must respect `--dry-run`.
4. **Batch resilience** — one project failure must not stop others.
5. **Report completeness** — every attempted project yields an `ExecutionResult` row.
6. **No secrets in reports/stdout** — never print `CHECKMARX_API_KEY` or tokens.
7. **Engine whitelist** — do not send unknown engine names to recalculate.

## Common failure modes

| Symptom | Likely cause |
|---------|----------------|
| `Realm does not exist` | Wrong `CHECKMARX_TENANT` |
| Auth 401/400 | Bad/expired API key or wrong region auth URL |
| Project not found | Excel name ≠ Checkmarx name |
| Multiple projects named… | Duplicate names in tenant; must be resolved in CxOne |
| Skipped: no engines | Completed scan metadata lacks extractable engines |

## Safe verification checklist

Before claiming a change works:

1. `python checkmarx_rescans.py --dry-run` against a small Excel sample.
2. Confirm new Summary/Results columns if schema changed.
3. Confirm skipped vs dry-run vs triggered counts match expectations.
4. Do not run live recalculation unless the user explicitly asks.

## External docs

- Checkmarx One authentication: https://checkmarx.stoplight.io/docs/checkmarx-one-api-reference-guide/ybti0d5u0auto-authentication

## Regions (reference)

| Region | `CHECKMARX_BASE_URL` | `CHECKMARX_AUTH_URL` |
|--------|----------------------|----------------------|
| US1 | `https://ast.checkmarx.net` | `https://iam.checkmarx.net` |
| US2 | `https://us.ast.checkmarx.net` | `https://us.iam.checkmarx.net` |
