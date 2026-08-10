import json
import os
import re

import requests
from dotenv import load_dotenv

from get_token import (
    get_authorization_header,
    main as fetch_access_token,
    refresh_access_token,
)

load_dotenv()

tenant_name = os.getenv("TENANT_NAME", "")
cod_app = os.getenv("COD_APP", "")
iam_base_url = os.getenv("IAM_BASE_URL", "iam.checkmarx.net")
ast_base_url = os.getenv("AST_BASE_URL", "ast.checkmarx.net")
oauth_client_info_file = "oauth_client_info.json"
plugin_scanner_group = os.getenv("PLUGIN_SCANNER_GROUP", "CHMX_PLUGINSCANNER")


def validate_inputs() -> None:
    if not tenant_name:
        raise ValueError("Missing TENANT_NAME in .env")
    if not cod_app:
        raise ValueError("Missing COD_APP in .env")
    if not plugin_scanner_group:
        raise ValueError("Missing PLUGIN_SCANNER_GROUP in .env")


def build_clients_url() -> str:
    return f"https://{iam_base_url}/auth/admin/realms/{tenant_name}/clients"


def build_retrieve_groups_url() -> str:
    return f"https://{ast_base_url}/api/access-management/groups"


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


def get_group_id_by_name(groups: list[dict], group_name: str) -> str:
    for group in groups:
        if group.get("name") == group_name:
            group_id = group.get("id")
            if group_id:
                return group_id
    raise ValueError(f"Group '{group_name}' not found in tenant.")


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


def save_oauth_client_info(data: dict) -> None:
    with open(oauth_client_info_file, "w", encoding="utf-8") as file:
        json.dump(data, file, indent=2)
    print(f"Stored OAuth client info in {oauth_client_info_file}")


def get_client_by_client_id(client_id: str) -> dict | None:
    response = request_with_refresh(
        "GET",
        build_clients_url(),
        headers={"Accept": "application/json"},
        params={"clientId": client_id},
    )
    if response.status_code != 200:
        print(f"Failed to lookup OAuth client {client_id}: {response.status_code}")
        print(response.text)
        response.raise_for_status()

    clients = response.json()
    if not clients:
        return None
    return clients[0]


def parse_client_id_from_location(location: str) -> str | None:
    match = re.search(r"/clients/([^/]+)$", location)
    return match.group(1) if match else None


def create_iam_oauth_client(client_id: str) -> dict:
    existing = get_client_by_client_id(client_id)
    if existing:
        print(f"OAuth client already exists: {client_id}")
        return existing

    response = request_with_refresh(
        "POST",
        build_clients_url(),
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
        json={
            "clientId": client_id,
            "name": client_id,
            "description": f"{client_id} Cuenta de Servicio para integraciones",
            "serviceAccountsEnabled": True,
        },
    )

    if response.status_code not in (200, 201):
        print(f"Failed to create OAuth client {client_id}: {response.status_code}")
        print(response.text)
        response.raise_for_status()

    created = get_client_by_client_id(client_id)
    if not created:
        location = response.headers.get("Location", "")
        internal_id = parse_client_id_from_location(location)
        if internal_id:
            created = {"id": internal_id, "clientId": client_id}
        else:
            raise ValueError(f"OAuth client {client_id} created but could not resolve id.")

    print(f"OAuth client created: {client_id}")
    return created


def get_client_by_internal_id(client_internal_id: str) -> dict:
    response = request_with_refresh(
        "GET",
        f"{build_clients_url()}/{client_internal_id}",
        headers={"Accept": "application/json"},
    )
    if response.status_code != 200:
        print(f"Failed to get OAuth client: {response.status_code}")
        print(response.text)
        response.raise_for_status()
    return response.json()


def enable_service_account(client_internal_id: str) -> None:
    client = get_client_by_internal_id(client_internal_id)
    if client.get("serviceAccountsEnabled"):
        return

    client["serviceAccountsEnabled"] = True
    response = request_with_refresh(
        "PUT",
        f"{build_clients_url()}/{client_internal_id}",
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
        json=client,
    )
    if response.status_code not in (200, 204):
        print(f"Failed enabling service account: {response.status_code}")
        print(response.text)
        response.raise_for_status()


def get_service_account_user_id(client_internal_id: str) -> str:
    response = request_with_refresh(
        "GET",
        f"{build_clients_url()}/{client_internal_id}/service-account-user",
        headers={"Accept": "application/json"},
    )
    if response.status_code != 200:
        print(f"Failed to get service account user: {response.status_code}")
        print(response.text)
        response.raise_for_status()
    return response.json().get("id", "")


def get_client_secret(client_internal_id: str) -> str:
    url = f"{build_clients_url()}/{client_internal_id}/client-secret"
    response = request_with_refresh(
        "GET",
        url,
        headers={"Accept": "application/json"},
    )

    if response.status_code == 404:
        response = request_with_refresh(
            "POST",
            url,
            headers={"Accept": "application/json"},
        )

    if response.status_code not in (200, 201):
        print(f"Failed to get OAuth client secret: {response.status_code}")
        print(response.text)
        response.raise_for_status()

    payload = response.json()
    secret = payload.get("value", "")
    if not secret:
        raise ValueError("OAuth client secret response did not include a value.")
    return secret


def add_client_to_groups(
    client_internal_id: str, group_assignments: list[tuple[str, str]]
) -> None:
    enable_service_account(client_internal_id)
    service_account_user_id = get_service_account_user_id(client_internal_id)
    if not service_account_user_id:
        raise ValueError("Service account user id is empty.")

    for group_id, group_name in group_assignments:
        response = request_with_refresh(
            "PUT",
            (
                f"https://{iam_base_url}/auth/admin/realms/{tenant_name}/users/"
                f"{service_account_user_id}/groups/{group_id}"
            ),
            headers={"Accept": "application/json"},
        )
        if response.status_code not in (200, 204):
            print(f"Failed adding OAuth client to {group_name}: {response.status_code}")
            print(response.text)
            response.raise_for_status()
        print(f"OAuth client service account added to {group_name}")


def main() -> None:
    validate_inputs()
    lead_group_name = f"CHMX_LEAD_{cod_app}_PROD"
    groups = list_groups()
    lead_group_id = get_group_id_by_name(groups, lead_group_name)
    plugin_scanner_group_id = get_group_id_by_name(groups, plugin_scanner_group)

    client = create_iam_oauth_client(cod_app)
    client_internal_id = client.get("id", "")
    if not client_internal_id:
        raise ValueError("OAuth client internal id is missing.")

    add_client_to_groups(
        client_internal_id,
        [
            (lead_group_id, lead_group_name),
            (plugin_scanner_group_id, plugin_scanner_group),
        ],
    )

    client_secret = get_client_secret(client_internal_id)
    print("OAuth client secret retrieved and stored in oauth_client_info.json")

    save_oauth_client_info(
        {
            "clientId": cod_app,
            "iamClientId": client_internal_id,
            "clientSecret": client_secret,
            "leadGroupName": lead_group_name,
            "leadGroupId": lead_group_id,
            "pluginScannerGroupName": plugin_scanner_group,
            "pluginScannerGroupId": plugin_scanner_group_id,
            "roleViaGroup": "plugin_scanner",
        }
    )


if __name__ == "__main__":
    main()
