#!/usr/bin/env python3
"""Add GitHub org name (from project name prefix) as a Checkmarx One project tag.

Project names are expected like: org/repo (e.g. itti-data/report-service).
The org segment becomes a key-only tag: {"itti-data": ""}.

Modes:
  --dry-run (default): plan tags and write CSV, no API writes
  --apply:             merge tags via PATCH and update CSV with results
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urljoin

import requests
from dotenv import load_dotenv

REGION_IAM = {
    "US": "iam.checkmarx.net",
    "US2": "us.iam.checkmarx.net",
}

REGION_API = {
    "US": "ast.checkmarx.net",
    "US2": "us.ast.checkmarx.net",
}

CSV_FIELDS = [
    "project_id",
    "project_name",
    "existing_tags",
    "org_tag",
    "action",
    "update_status",
    "error",
]

SCRIPT_DIR = Path(__file__).resolve().parent


@dataclass
class Settings:
    tenant: str
    region: str
    iam_host: str
    api_host: str
    client_id: str
    client_secret: str


@dataclass
class ProjectRow:
    project_id: str
    project_name: str
    existing_tags: dict[str, Any]
    org_tag: str
    action: str
    update_status: str
    error: str = ""
    merged_tags: dict[str, Any] = field(default_factory=dict)

    def to_csv_dict(self) -> dict[str, str]:
        return {
            "project_id": self.project_id,
            "project_name": self.project_name,
            "existing_tags": json.dumps(self.existing_tags, ensure_ascii=False),
            "org_tag": self.org_tag,
            "action": self.action,
            "update_status": self.update_status,
            "error": self.error,
        }


def load_settings() -> Settings:
    tenant = os.environ.get("CX_TENANT", "").strip()
    client_id = os.environ.get("CX_CLIENT_ID", "").strip()
    client_secret = os.environ.get("CX_CLIENT_SECRET", "").strip()
    region = os.getenv("CX_REGION", "US2").strip().upper()

    if not tenant:
        raise ValueError("CX_TENANT is required in .env")
    if not client_id or not client_secret:
        raise ValueError("CX_CLIENT_ID and CX_CLIENT_SECRET are required in .env")

    iam_host = os.getenv("CX_IAM_HOST") or REGION_IAM.get(region)
    api_host = os.getenv("CX_API_HOST") or REGION_API.get(region)
    if not iam_host or not api_host:
        raise ValueError(
            f"Unknown CX_REGION '{region}'. Use US or US2, "
            "or set CX_IAM_HOST and CX_API_HOST."
        )

    return Settings(
        tenant=tenant,
        region=region,
        iam_host=iam_host,
        api_host=api_host,
        client_id=client_id,
        client_secret=client_secret,
    )


class CxOneClient:
    def __init__(self, settings: Settings, timeout: int = 60):
        self.settings = settings
        self.timeout = timeout
        self._token: Optional[str] = None
        self._token_expires_at = 0.0
        self.api_base = f"https://{settings.api_host}/api/"
        self.auth_url = (
            f"https://{settings.iam_host}/auth/realms/{settings.tenant}"
            "/protocol/openid-connect/token"
        )

    def _authenticate(self) -> None:
        data = {
            "grant_type": "client_credentials",
            "client_id": self.settings.client_id,
            "client_secret": self.settings.client_secret,
        }
        resp = requests.post(
            self.auth_url,
            data=data,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=self.timeout,
        )
        resp.raise_for_status()
        body = resp.json()
        self._token = body["access_token"]
        self._token_expires_at = time.time() + int(body.get("expires_in", 300)) - 30

    def _headers(self) -> dict[str, str]:
        if not self._token or time.time() >= self._token_expires_at:
            self._authenticate()
        return {
            "Authorization": f"Bearer {self._token}",
            "Accept": "*/*; version=1.0",
            "Content-Type": "application/json",
            "User-Agent": "name-based-project-tag-script",
        }

    def request(self, method: str, path: str, **kwargs) -> requests.Response:
        url = urljoin(self.api_base, path.lstrip("/"))
        headers = kwargs.pop("headers", {})
        merged = {**self._headers(), **headers}

        resp: Optional[requests.Response] = None
        for attempt in range(5):
            try:
                resp = requests.request(
                    method, url, headers=merged, timeout=self.timeout, **kwargs
                )
            except requests.RequestException:
                if attempt < 4:
                    time.sleep(min(2**attempt, 30))
                    continue
                raise

            if resp.status_code == 401:
                self._authenticate()
                merged = {**self._headers(), **headers}
                continue
            if resp.status_code in (429, 503) and attempt < 4:
                time.sleep(min(2**attempt, 30))
                continue
            return resp
        assert resp is not None
        return resp

    def get(self, path: str, **kwargs) -> requests.Response:
        return self.request("GET", path, **kwargs)

    def patch(self, path: str, **kwargs) -> requests.Response:
        return self.request("PATCH", path, **kwargs)

    def list_projects(self) -> list[dict[str, Any]]:
        projects: list[dict[str, Any]] = []
        offset = 0
        page_size = 500
        while True:
            resp = self.get("projects/", params={"limit": page_size, "offset": offset})
            resp.raise_for_status()
            data = resp.json()
            batch = data.get("projects", [])
            projects.extend(batch)
            total = data.get("filteredTotalCount", data.get("totalCount", 0))
            offset += len(batch)
            if not batch or offset >= total:
                break
        return projects

    def update_project_tags(
        self, project_id: str, tags: dict[str, Any]
    ) -> requests.Response:
        return self.patch(f"projects/{project_id}", json={"tags": tags})


def extract_org(project_name: str) -> Optional[str]:
    if "/" not in project_name:
        return None
    org = project_name.split("/", 1)[0].strip()
    return org or None


def build_rows(projects: list[dict[str, Any]], apply: bool) -> list[ProjectRow]:
    rows: list[ProjectRow] = []
    for project in projects:
        project_id = str(project.get("id", ""))
        project_name = str(project.get("name", ""))
        existing_tags = project.get("tags") or {}
        if not isinstance(existing_tags, dict):
            existing_tags = {}

        org = extract_org(project_name)
        if not org:
            rows.append(
                ProjectRow(
                    project_id=project_id,
                    project_name=project_name,
                    existing_tags=existing_tags,
                    org_tag="",
                    action="skip_no_org",
                    update_status="skipped",
                )
            )
            continue

        if org in existing_tags:
            rows.append(
                ProjectRow(
                    project_id=project_id,
                    project_name=project_name,
                    existing_tags=existing_tags,
                    org_tag=org,
                    action="skip_exists",
                    update_status="skipped",
                )
            )
            continue

        merged = {**existing_tags, org: ""}
        rows.append(
            ProjectRow(
                project_id=project_id,
                project_name=project_name,
                existing_tags=existing_tags,
                org_tag=org,
                action="add",
                update_status="pending" if apply else "dry_run",
                merged_tags=merged,
            )
        )
    return rows


def write_csv(path: Path, rows: list[ProjectRow]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=CSV_FIELDS)
        writer.writeheader()
        for row in rows:
            writer.writerow(row.to_csv_dict())


def apply_updates(client: CxOneClient, rows: list[ProjectRow], csv_path: Path) -> None:
    actionable = [r for r in rows if r.action == "add"]
    total = len(actionable)
    for idx, row in enumerate(actionable, start=1):
        print(f"[{idx}/{total}] Updating {row.project_name} (+{row.org_tag})...")
        try:
            resp = client.update_project_tags(row.project_id, row.merged_tags)
            if resp.ok:
                row.update_status = "updated"
                row.error = ""
            else:
                row.update_status = "failed"
                row.error = f"{resp.status_code}: {resp.text[:300]}"
        except requests.RequestException as exc:
            row.update_status = "failed"
            row.error = str(exc)[:300]

        # Persist progress after each update so a mid-run interrupt keeps results
        write_csv(csv_path, rows)


def print_summary(rows: list[ProjectRow], apply: bool) -> None:
    counts = {
        "total": len(rows),
        "add": sum(1 for r in rows if r.action == "add"),
        "skip_exists": sum(1 for r in rows if r.action == "skip_exists"),
        "skip_no_org": sum(1 for r in rows if r.action == "skip_no_org"),
        "updated": sum(1 for r in rows if r.update_status == "updated"),
        "failed": sum(1 for r in rows if r.update_status == "failed"),
        "dry_run": sum(1 for r in rows if r.update_status == "dry_run"),
    }
    mode = "apply" if apply else "dry-run"
    print("\n=== Summary ===")
    print(f"Mode:            {mode}")
    print(f"Total projects:  {counts['total']}")
    print(f"To add tag:      {counts['add']}")
    print(f"Skip (exists):   {counts['skip_exists']}")
    print(f"Skip (no org):   {counts['skip_no_org']}")
    if apply:
        print(f"Updated:         {counts['updated']}")
        print(f"Failed:          {counts['failed']}")
    else:
        print(f"Dry-run planned: {counts['dry_run']}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Extract GitHub org from CxOne project names (org/repo) "
            "and add it as a project tag."
        )
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--dry-run",
        dest="apply",
        action="store_false",
        help="Plan tags and write CSV only (default)",
    )
    mode.add_argument(
        "--apply",
        dest="apply",
        action="store_true",
        help="Apply tag updates via PATCH and record results in CSV",
    )
    parser.set_defaults(apply=False)
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="CSV output path (default: output/org_tags_YYYYMMDD_HHMMSS.csv)",
    )
    return parser.parse_args()


def main() -> int:
    load_dotenv(SCRIPT_DIR / ".env")
    args = parse_args()
    apply = bool(args.apply)

    try:
        settings = load_settings()
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    csv_path = args.output or (SCRIPT_DIR / "output" / f"org_tags_{stamp}.csv")

    print(f"Tenant:  {settings.tenant}")
    print(f"Region:  {settings.region} ({settings.api_host})")
    print(f"Mode:    {'apply' if apply else 'dry-run'}")
    print(f"Output:  {csv_path}")

    client = CxOneClient(settings)
    print("Authenticating...")
    client._authenticate()
    print("Listing projects...")
    projects = client.list_projects()
    print(f"Found {len(projects)} projects")

    rows = build_rows(projects, apply=apply)
    write_csv(csv_path, rows)
    print(f"Wrote planned CSV: {csv_path}")

    if apply:
        print("Applying tag updates...")
        apply_updates(client, rows, csv_path)
        write_csv(csv_path, rows)
        print(f"Updated CSV: {csv_path}")

    print_summary(rows, apply=apply)
    return 0 if not any(r.update_status == "failed" for r in rows) else 2


if __name__ == "__main__":
    sys.exit(main())
