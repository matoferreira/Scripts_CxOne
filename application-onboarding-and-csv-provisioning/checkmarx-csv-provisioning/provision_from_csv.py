"""
Provision Checkmarx One applications, groups, and group-to-application
assignments from a CSV file.

Expected columns (header row, case-insensitive):
  Application, Description, Tag, Type, Group 1, Group 2
"""

import argparse
import csv
import json
import os
import uuid
from datetime import datetime, timedelta, timezone

import requests
from dotenv import load_dotenv

load_dotenv()

TENANT_NAME = os.getenv("TENANT_NAME", "")
OAUTH_CLIENT = os.getenv("OAUTH_CLIENT", "")
SECRET_KEY = os.getenv("SECRET_KEY", "")
AST_BASE_URL = os.getenv("AST_BASE_URL", "ast.checkmarx.net")
IAM_BASE_URL = os.getenv("IAM_BASE_URL", "iam.checkmarx.net")
DEFAULT_CSV = os.getenv("APP_CSV_PATH", "apps_cx.csv")
RESULTS_FILE = os.getenv("BULK_PROVISION_RESULTS_FILE", "bulk_provision_results.json")
TOKEN_CACHE_FILE = "token_cache.json"

COLUMN_ALIASES = {
    "application": "application",
    "description": "description",
    "tag": "tag",
    "type": "type",
    "group 1": "group_1",
    "group1": "group_1",
    "group 2": "group_2",
    "group2": "group_2",
}


def validate_env() -> None:
    if not TENANT_NAME or not OAUTH_CLIENT or not SECRET_KEY:
        raise ValueError(
            "Missing env vars. Set TENANT_NAME, OAUTH_CLIENT, and SECRET_KEY in .env"
        )


def build_token_url() -> str:
    return (
        f"https://{IAM_BASE_URL}/auth/realms/"
        f"{TENANT_NAME}/protocol/openid-connect/token"
    )


def save_tokens(token_payload: dict) -> None:
    now = datetime.now(timezone.utc)
    expires_in = int(token_payload.get("expires_in", 0))
    refresh_expires_in = int(token_payload.get("refresh_expires_in", 0))
    cache_data = {
        "access_token": token_payload.get("access_token", ""),
        "refresh_token": token_payload.get("refresh_token", ""),
        "token_type": token_payload.get("token_type", "bearer"),
        "scope": token_payload.get("scope", ""),
        "access_token_expires_at": (now + timedelta(seconds=expires_in)).isoformat(),
        "refresh_token_expires_at": (
            (now + timedelta(seconds=refresh_expires_in)).isoformat()
            if refresh_expires_in > 0
            else None
        ),
        "saved_at": now.isoformat(),
    }
    with open(TOKEN_CACHE_FILE, "w", encoding="utf-8") as file:
        json.dump(cache_data, file, indent=2)


def load_cached_tokens() -> dict:
    if not os.path.exists(TOKEN_CACHE_FILE):
        raise FileNotFoundError(f"Token cache not found: {TOKEN_CACHE_FILE}")
    with open(TOKEN_CACHE_FILE, "r", encoding="utf-8") as file:
        return json.load(file)


def get_authorization_header() -> dict:
    token_data = load_cached_tokens()
    access_token = token_data.get("access_token", "")
    token_type = token_data.get("token_type", "bearer")
    if not access_token:
        raise ValueError("Cached token file exists but access_token is empty.")
    return {"Authorization": f"{token_type.capitalize()} {access_token}"}


def fetch_access_token() -> dict:
    response = requests.post(
        build_token_url(),
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "application/json",
        },
        data={
            "client_id": OAUTH_CLIENT,
            "grant_type": "client_credentials",
            "client_secret": SECRET_KEY,
        },
        timeout=30,
    )
    if response.status_code != 200:
        print(f"Token request failed: {response.status_code}")
        print(response.text)
        response.raise_for_status()
    payload = response.json()
    save_tokens(payload)
    return payload


def refresh_access_token() -> dict:
    cached = load_cached_tokens()
    refresh_token = cached.get("refresh_token", "")
    if not refresh_token:
        raise ValueError("No refresh_token available in token cache.")

    response = requests.post(
        build_token_url(),
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "application/json",
        },
        data={
            "client_id": OAUTH_CLIENT,
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_secret": SECRET_KEY,
        },
        timeout=30,
    )
    if response.status_code != 200:
        print(f"Refresh token request failed: {response.status_code}")
        print(response.text)
        response.raise_for_status()
    payload = response.json()
    save_tokens(payload)
    return payload


def request_with_refresh(method: str, url: str, **kwargs) -> requests.Response:
    headers = kwargs.pop("headers", {})
    merged_headers = {**headers, **get_authorization_header()}
    response = requests.request(method, url, headers=merged_headers, timeout=30, **kwargs)

    if response.status_code == 401:
        try:
            refresh_access_token()
        except Exception:
            fetch_access_token()
        merged_headers = {**headers, **get_authorization_header()}
        response = requests.request(
            method, url, headers=merged_headers, timeout=30, **kwargs
        )

    return response


def normalize_row(row: dict[str, str | None]) -> dict[str, str]:
    record: dict[str, str] = {}
    for header, value in row.items():
        if header is None:
            continue
        normalized = COLUMN_ALIASES.get(header.strip().lower())
        if normalized and value is not None:
            record[normalized] = str(value).strip()
    return record


def read_csv_records(path: str) -> list[dict[str, str]]:
    if not os.path.exists(path):
        raise FileNotFoundError(f"CSV file not found: {path}")

    records: list[dict[str, str]] = []
    with open(path, newline="", encoding="utf-8-sig") as file:
        reader = csv.DictReader(file)
        if not reader.fieldnames:
            raise ValueError("CSV file must contain a header row.")

        for row in reader:
            record = normalize_row(row)
            if any(record.values()):
                records.append(record)

    if not records:
        raise ValueError("No data rows found in CSV file.")

    required = ["application", "description", "tag", "type", "group_1", "group_2"]
    missing = [field for field in required if field not in records[0]]
    if missing:
        raise ValueError(f"Missing required columns in CSV header: {missing}")

    return records


def build_project_rule(tag_value: str) -> dict:
    if ";" in tag_value:
        return {"type": "project.tag.key-value.exists", "value": tag_value}
    return {"type": "project.tag.key.exists", "value": tag_value}


def build_application_tags(tag_value: str) -> dict:
    if ";" in tag_value:
        key, value = tag_value.split(";", 1)
        return {key.strip(): value.strip()}
    return {tag_value: ""}


def list_all_applications(page_size: int = 100) -> list[dict]:
    apps: list[dict] = []
    offset = 0
    url = f"https://{AST_BASE_URL}/api/applications/"

    while True:
        response = request_with_refresh(
            "GET",
            url,
            headers={
                "Accept": "application/json; version=1.0",
                "CorrelationId": str(uuid.uuid4()),
            },
            params={"offset": offset, "limit": page_size},
        )
        if response.status_code != 200:
            print(f"List applications failed: {response.status_code}")
            print(response.text)
            response.raise_for_status()

        payload = response.json()
        page_apps = payload.get("applications", [])
        total_count = int(payload.get("totalCount", 0))
        apps.extend(page_apps)
        if not page_apps or len(apps) >= total_count:
            break
        offset += len(page_apps)

    return apps


def list_groups() -> list[dict]:
    response = request_with_refresh(
        "GET",
        f"https://{AST_BASE_URL}/api/access-management/groups",
        headers={"Accept": "application/json"},
    )
    if response.status_code != 200:
        print(f"List groups failed: {response.status_code}")
        print(response.text)
        response.raise_for_status()
    return response.json()


def ensure_application(record: dict, existing_apps: list[dict]) -> tuple[dict, bool]:
    name = record["application"]
    existing = next((app for app in existing_apps if app.get("name") == name), None)
    if existing:
        print(f"Application already exists: {name}")
        return existing, False

    tag_value = record["tag"]
    payload = {
        "name": name,
        "description": record["description"],
        "type": record["type"],
        "criticality": 3,
        "rules": [build_project_rule(tag_value)],
        "tags": build_application_tags(tag_value),
    }

    response = request_with_refresh(
        "POST",
        f"https://{AST_BASE_URL}/api/applications/",
        headers={
            "Accept": "application/json; version=1.0",
            "Content-Type": "application/json; version=1.0",
            "CorrelationId": str(uuid.uuid4()),
        },
        json=payload,
    )
    if response.status_code not in (200, 201):
        print(f"Create application failed for {name}: {response.status_code}")
        print(response.text)
        response.raise_for_status()

    created = response.json()
    print(f"Application created: {name}")
    return created, True


def ensure_group(group_name: str, existing_groups: list[dict]) -> tuple[str, bool]:
    existing = next((g for g in existing_groups if g.get("name") == group_name), None)
    if existing:
        print(f"Group already exists: {group_name}")
        return existing.get("id", ""), False

    response = request_with_refresh(
        "POST",
        f"https://{IAM_BASE_URL}/auth/admin/realms/{TENANT_NAME}/groups",
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
        json={
            "name": group_name,
            "description": f"Group created from CSV provisioning for {group_name}",
        },
    )
    if response.status_code != 201:
        print(f"Create group failed for {group_name}: {response.status_code}")
        print(response.text)
        response.raise_for_status()

    print(f"Group created: {group_name}")
    refreshed = list_groups()
    created = next((g for g in refreshed if g.get("name") == group_name), None)
    if not created or not created.get("id"):
        raise ValueError(f"Group {group_name} created but id not found.")
    return created["id"], True


def assign_group_to_application(group_id: str, group_name: str, application_id: str) -> None:
    response = request_with_refresh(
        "POST",
        f"https://{AST_BASE_URL}/api/access-management/",
        headers={
            "Accept": "application/json",
            "Content-Type": "application/json",
        },
        json={
            "entityID": group_id,
            "entityType": "group",
            "resourceID": application_id,
            "resourceType": "application",
        },
    )
    if response.status_code not in (200, 201, 204):
        print(
            f"Assign group {group_name} to application failed: {response.status_code}"
        )
        print(response.text)
        response.raise_for_status()
    print(f"Assigned group {group_name} to application")


def process_record(record: dict) -> dict:
    apps = list_all_applications()
    groups = list_groups()

    application, app_created = ensure_application(record, apps)
    application_id = application.get("id", "")
    if not application_id:
        raise ValueError(f"Application id missing for {record['application']}")

    group1_id, group1_created = ensure_group(record["group_1"], groups)
    groups = list_groups()
    group2_id, group2_created = ensure_group(record["group_2"], groups)

    assign_group_to_application(group1_id, record["group_1"], application_id)
    assign_group_to_application(group2_id, record["group_2"], application_id)

    return {
        "application": record["application"],
        "applicationId": application_id,
        "applicationCreated": app_created,
        "group1": record["group_1"],
        "group1Id": group1_id,
        "group1Created": group1_created,
        "group2": record["group_2"],
        "group2Id": group2_id,
        "group2Created": group2_created,
        "status": "success",
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Provision Checkmarx applications and groups from a CSV file."
    )
    parser.add_argument(
        "--file",
        default=DEFAULT_CSV,
        help=f"Path to .csv input file (default: {DEFAULT_CSV})",
    )
    args = parser.parse_args()

    validate_env()
    try:
        get_authorization_header()
    except Exception:
        fetch_access_token()
        print("Token fetched and cached.")

    records = read_csv_records(args.file)
    print(f"Loaded {len(records)} row(s) from {args.file}")

    results = []
    for index, record in enumerate(records, start=1):
        print(f"\n--- Processing row {index}: {record['application']} ---")
        try:
            results.append(process_record(record))
        except Exception as exc:
            print(f"Row {index} failed: {exc}")
            results.append(
                {
                    "application": record.get("application", ""),
                    "status": "failed",
                    "error": str(exc),
                }
            )

    output = {
        "processedAt": datetime.now(timezone.utc).isoformat(),
        "sourceFile": args.file,
        "results": results,
    }
    with open(RESULTS_FILE, "w", encoding="utf-8") as file:
        json.dump(output, file, indent=2)
    print(f"\nResults saved to {RESULTS_FILE}")


if __name__ == "__main__":
    main()
