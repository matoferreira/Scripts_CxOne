import json
import os

import requests
from dotenv import load_dotenv

from get_token import get_authorization_header, refresh_access_token

load_dotenv()

cod_app = os.getenv("COD_APP", "")
ast_base_url = os.getenv("AST_BASE_URL", "ast.checkmarx.net")
group_ids_file = "group_ids.json"


def validate_inputs() -> None:
    if not cod_app:
        raise ValueError("Missing COD_APP in .env")
    if not os.path.exists(group_ids_file):
        raise FileNotFoundError(
            "group_ids.json not found. Run create_groups.py first."
        )


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


def load_group_ids() -> dict:
    with open(group_ids_file, "r", encoding="utf-8") as file:
        return json.load(file)


def assign_base_role(entity_id: str, base_role: str) -> None:
    url = f"https://{ast_base_url}/api/access-management/base-roles/{entity_id}"
    payload = {"baseRoles": [base_role]}
    response = request_with_refresh(
        "POST",
        url,
        headers={
            "Accept": "application/json",
            "Content-Type": "application/json",
        },
        json=payload,
    )

    if response.status_code not in (200, 201, 204):
        print(f"Failed assigning role {base_role} to entity {entity_id}: {response.status_code}")
        print(response.text)
        response.raise_for_status()


def main() -> None:
    validate_inputs()
    group_ids = load_group_ids()

    dev_group_name = f"CHMX_DEV_{cod_app}_PROD"
    lead_group_name = f"CHMX_LEAD_{cod_app}_PROD"

    dev_group_id = group_ids.get(dev_group_name)
    lead_group_id = group_ids.get(lead_group_name)

    if not dev_group_id:
        raise ValueError(f"Missing group id for {dev_group_name} in group_ids.json")
    if not lead_group_id:
        raise ValueError(f"Missing group id for {lead_group_name} in group_ids.json")

    assign_base_role(dev_group_id, "chmx_dev_base_role")
    print(f"Assigned chmx_dev_base_role to {dev_group_name}")

    assign_base_role(lead_group_id, "chmx_lead_base_role")
    print(f"Assigned chmx_lead_base_role to {lead_group_name}")


if __name__ == "__main__":
    main()
