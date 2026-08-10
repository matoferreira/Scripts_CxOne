import json
import os
import uuid

import requests
from dotenv import load_dotenv

from get_token import (
    get_authorization_header,
    main as fetch_access_token,
    refresh_access_token,
)

load_dotenv()

cod_app = os.getenv("COD_APP", "")
ast_base_url = os.getenv("AST_BASE_URL", "ast.checkmarx.net")
application_info_file = "application_info.json"


def validate_inputs() -> None:
    if not cod_app:
        raise ValueError("Missing COD_APP in .env")


def build_applications_url() -> str:
    return f"https://{ast_base_url}/api/applications/"


def request_with_refresh(method: str, url: str, **kwargs) -> requests.Response:
    headers = kwargs.pop("headers", {})
    merged_headers = {**headers, **get_authorization_header()}
    response = requests.request(method, url, headers=merged_headers, timeout=30, **kwargs)

    if response.status_code == 401:
        try:
            refresh_access_token()
        except Exception:
            # Some tenants return refresh_expires_in=0; fallback to client credentials.
            fetch_access_token()
        merged_headers = {**headers, **get_authorization_header()}
        response = requests.request(
            method, url, headers=merged_headers, timeout=30, **kwargs
        )

    return response


def list_all_applications(page_size: int = 100) -> list[dict]:
    applications: list[dict] = []
    offset = 0

    while True:
        correlation_id = str(uuid.uuid4())
        response = request_with_refresh(
            "GET",
            build_applications_url(),
            headers={
                "Accept": "application/json; version=1.0",
                "CorrelationId": correlation_id,
            },
            params={
                "offset": offset,
                "limit": page_size,
            },
        )

        if response.status_code != 200:
            print(f"Failed to list applications: {response.status_code}")
            print(response.text)
            response.raise_for_status()

        payload = response.json()
        page_apps = payload.get("applications", [])
        total_count = int(payload.get("totalCount", 0))

        applications.extend(page_apps)

        if not page_apps or len(applications) >= total_count:
            break

        offset += len(page_apps)

    return applications


def find_application_by_name(name: str, applications: list[dict]) -> dict | None:
    for app in applications:
        if app.get("name") == name:
            return app
    return None


def create_application(name: str) -> dict:
    correlation_id = str(uuid.uuid4())
    response = request_with_refresh(
        "POST",
        build_applications_url(),
        headers={
            "Accept": "application/json; version=1.0",
            "Content-Type": "application/json; version=1.0",
            "CorrelationId": correlation_id,
        },
        json={
            "name": name,
            "description": "",
            "criticality": 3,
            "rules": [
                {
                    "type": "project.tag.key.exists",
                    "value": cod_app,
                }
            ],
            "tags": {},
        },
    )

    if response.status_code not in (200, 201):
        print(f"Failed to create application: {response.status_code}")
        print(response.text)
        response.raise_for_status()

    return response.json()


def save_application_info(application: dict, created_now: bool) -> None:
    output = {
        "id": application.get("id"),
        "name": application.get("name"),
        "created_now": created_now,
    }
    with open(application_info_file, "w", encoding="utf-8") as file:
        json.dump(output, file, indent=2)
    print(f"Stored application info in {application_info_file}")


def main() -> None:
    validate_inputs()
    applications = list_all_applications(page_size=100)
    existing = find_application_by_name(cod_app, applications)

    if existing:
        print(f"Application already exists: {cod_app}")
        save_application_info(existing, created_now=False)
        return

    created = create_application(cod_app)
    print(f"Application created: {cod_app}")
    save_application_info(created, created_now=True)


if __name__ == "__main__":
    main()
