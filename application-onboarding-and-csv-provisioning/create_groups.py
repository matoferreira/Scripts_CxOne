import os
import json

import requests
from dotenv import load_dotenv

from get_token import get_authorization_header, refresh_access_token

load_dotenv()

tenant_name = os.getenv("TENANT_NAME", "")
cod_app = os.getenv("COD_APP", "")
ast_base_url = os.getenv("AST_BASE_URL", "ast.checkmarx.net")
iam_base_url = os.getenv("IAM_BASE_URL", "iam.checkmarx.net")
group_ids_file = "group_ids.json"


def validate_inputs() -> None:
    if not tenant_name:
        raise ValueError("Missing TENANT_NAME in .env")
    if not cod_app:
        raise ValueError("Missing COD_APP in .env")


def build_groups_url() -> str:
    return f"https://{iam_base_url}/auth/admin/realms/{tenant_name}/groups"


def build_retrieve_groups_url() -> str:
    return f"https://{ast_base_url}/api/access-management/groups"


def request_with_refresh(method: str, url: str, **kwargs) -> requests.Response:
    headers = kwargs.pop("headers", {})
    merged_headers = {**headers, **get_authorization_header()}
    response = requests.request(method, url, headers=merged_headers, timeout=30, **kwargs)

    if response.status_code == 401:
        refresh_access_token()
        merged_headers = {**headers, **get_authorization_header()}
        response = requests.request(
            method, url, headers=merged_headers, timeout=30, **kwargs
        )

    return response


def list_groups() -> list[dict]:
    response = request_with_refresh(
        "GET",
        build_retrieve_groups_url(),
        headers={"Accept": "application/json"},
    )
    if response.status_code != 200:
        print(f"Failed to list groups: {response.status_code}")
        print(response.text)
        response.raise_for_status()
    return response.json()


def save_group_ids(group_names: list[str], groups: list[dict]) -> None:
    group_id_map = {}
    for group in groups:
        name = group.get("name")
        if name in group_names:
            group_id_map[name] = group.get("id")

    missing = [name for name in group_names if not group_id_map.get(name)]
    if missing:
        raise ValueError(f"Could not find IDs for groups: {missing}")

    with open(group_ids_file, "w", encoding="utf-8") as file:
        json.dump(group_id_map, file, indent=2)

    print(f"Stored group IDs in {group_ids_file}")


def create_group(group_name: str, description: str) -> None:
    url = build_groups_url()
    payload = {
        "name": group_name,
        "description": description,
    }
    response = request_with_refresh(
        "POST",
        url,
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
        json=payload,
    )

    if response.status_code != 201:
        print(f"Failed to create group {group_name}: {response.status_code}")
        print(response.text)
        response.raise_for_status()

    print(f"Created group: {group_name} (201)")


def main() -> None:
    validate_inputs()

    groups_to_create = [
        f"CHMX_DEV_{cod_app}_PROD",
        f"CHMX_LEAD_{cod_app}_PROD",
    ]

    existing_groups = list_groups()
    existing_names = {group.get("name") for group in existing_groups}

    for group_name in groups_to_create:
        if group_name in existing_names:
            print(f"Group already exists, skipping create: {group_name}")
            continue
        create_group(group_name, "Group created via API automation")

    final_groups = list_groups()
    save_group_ids(groups_to_create, final_groups)


if __name__ == "__main__":
    main()
