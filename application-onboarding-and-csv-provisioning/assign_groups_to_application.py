import json
import os

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
group_ids_file = "group_ids.json"
application_info_file = "application_info.json"


def validate_inputs() -> None:
    if not cod_app:
        raise ValueError("Missing COD_APP in .env")
    if not os.path.exists(group_ids_file):
        raise FileNotFoundError("group_ids.json not found. Run create_groups.py first.")
    if not os.path.exists(application_info_file):
        raise FileNotFoundError(
            "application_info.json not found. Run create_application.py first."
        )


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


def load_json(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as file:
        return json.load(file)


def create_assignment(entity_id: str, resource_id: str) -> None:
    response = request_with_refresh(
        "POST",
        f"https://{ast_base_url}/api/access-management/",
        headers={
            "Accept": "application/json",
            "Content-Type": "application/json",
        },
        json={
            "entityID": entity_id,
            "entityType": "group",
            "resourceID": resource_id,
            "resourceType": "application",
        },
    )

    if response.status_code not in (200, 201, 204):
        print(f"Failed assignment for entity {entity_id}: {response.status_code}")
        print(response.text)
        response.raise_for_status()


def main() -> None:
    validate_inputs()

    group_ids = load_json(group_ids_file)
    app_info = load_json(application_info_file)

    dev_group_name = f"CHMX_DEV_{cod_app}_PROD"
    lead_group_name = f"CHMX_LEAD_{cod_app}_PROD"
    dev_group_id = group_ids.get(dev_group_name)
    lead_group_id = group_ids.get(lead_group_name)
    application_id = app_info.get("id")

    if not dev_group_id:
        raise ValueError(f"Missing group id for {dev_group_name} in group_ids.json")
    if not lead_group_id:
        raise ValueError(f"Missing group id for {lead_group_name} in group_ids.json")
    if not application_id:
        raise ValueError("Missing application id in application_info.json")

    create_assignment(dev_group_id, application_id)
    print(f"Assigned {dev_group_name} to application")

    create_assignment(lead_group_id, application_id)
    print(f"Assigned {lead_group_name} to application")


if __name__ == "__main__":
    main()
