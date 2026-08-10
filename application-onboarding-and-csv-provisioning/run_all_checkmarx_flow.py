import json
import os
import re
import uuid
from datetime import datetime, timedelta, timezone

import requests
from dotenv import load_dotenv

load_dotenv()

TENANT_NAME = os.getenv("TENANT_NAME", "")
OAUTH_CLIENT = os.getenv("OAUTH_CLIENT", "")
SECRET_KEY = os.getenv("SECRET_KEY", "")
COD_APP = os.getenv("COD_APP", "")
AST_BASE_URL = os.getenv("AST_BASE_URL", "ast.checkmarx.net")
IAM_BASE_URL = os.getenv("IAM_BASE_URL", "iam.checkmarx.net")
CONTINUE_ON_ROLE_ASSIGNMENT_ERROR = (
    os.getenv("CONTINUE_ON_ROLE_ASSIGNMENT_ERROR", "false").lower() == "true"
)

TOKEN_CACHE_FILE = "token_cache.json"
GROUP_IDS_FILE = "group_ids.json"
APPLICATION_INFO_FILE = "application_info.json"
OAUTH_CLIENT_INFO_FILE = "oauth_client_info.json"
PLUGIN_SCANNER_GROUP = os.getenv("PLUGIN_SCANNER_GROUP", "CHMX_PLUGINSCANNER")


def validate_inputs() -> None:
    if not TENANT_NAME or not OAUTH_CLIENT or not SECRET_KEY or not COD_APP:
        raise ValueError(
            "Missing env vars. Set TENANT_NAME, OAUTH_CLIENT, SECRET_KEY and COD_APP in .env"
        )
    if not PLUGIN_SCANNER_GROUP:
        raise ValueError("Missing PLUGIN_SCANNER_GROUP in .env")


def token_url() -> str:
    return (
        f"https://{IAM_BASE_URL}/auth/realms/"
        f"{TENANT_NAME}/protocol/openid-connect/token"
    )


def fetch_access_token() -> dict:
    response = requests.post(
        token_url(),
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
    cached = load_json(TOKEN_CACHE_FILE)
    refresh_token = cached.get("refresh_token", "")
    if not refresh_token:
        raise ValueError("No refresh_token available in token cache.")

    response = requests.post(
        token_url(),
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
    write_json(TOKEN_CACHE_FILE, cache_data)


def get_auth_header() -> dict:
    if not os.path.exists(TOKEN_CACHE_FILE):
        fetch_access_token()
    token_data = load_json(TOKEN_CACHE_FILE)
    access_token = token_data.get("access_token", "")
    token_type = token_data.get("token_type", "bearer")
    if not access_token:
        fetch_access_token()
        token_data = load_json(TOKEN_CACHE_FILE)
        access_token = token_data.get("access_token", "")
        token_type = token_data.get("token_type", "bearer")
    return {"Authorization": f"{token_type.capitalize()} {access_token}"}


def request_with_refresh(method: str, url: str, **kwargs) -> requests.Response:
    headers = kwargs.pop("headers", {})
    merged_headers = {**headers, **get_auth_header()}
    response = requests.request(method, url, headers=merged_headers, timeout=30, **kwargs)
    if response.status_code == 401:
        try:
            refresh_access_token()
        except Exception:
            fetch_access_token()
        merged_headers = {**headers, **get_auth_header()}
        response = requests.request(
            method, url, headers=merged_headers, timeout=30, **kwargs
        )
    return response


def load_json(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as file:
        return json.load(file)


def write_json(path: str, data: dict) -> None:
    with open(path, "w", encoding="utf-8") as file:
        json.dump(data, file, indent=2)


def ensure_groups_from_list(groups: list[dict]) -> dict:
    target_groups = [
        f"CHMX_DEV_{COD_APP}_PROD",
        f"CHMX_LEAD_{COD_APP}_PROD",
    ]

    existing = {g.get("name") for g in groups}

    for name in target_groups:
        if name in existing:
            print(f"Group already exists: {name}")
            continue
        create_group(name, "Group created via API automation")
        print(f"Group created: {name}")

    groups_after = list_groups()
    group_map = {}
    for g in groups_after:
        if g.get("name") in target_groups:
            group_map[g["name"]] = g.get("id")

    missing = [name for name in target_groups if not group_map.get(name)]
    if missing:
        raise ValueError(f"Missing expected groups after creation: {missing}")

    write_json(GROUP_IDS_FILE, group_map)
    print(f"Stored group IDs in {GROUP_IDS_FILE}")
    return group_map


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


def create_group(name: str, description: str) -> None:
    response = request_with_refresh(
        "POST",
        f"https://{IAM_BASE_URL}/auth/admin/realms/{TENANT_NAME}/groups",
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
        json={"name": name, "description": description},
    )
    if response.status_code != 201:
        print(f"Create group failed for {name}: {response.status_code}")
        print(response.text)
        response.raise_for_status()


def build_iam_clients_url() -> str:
    return f"https://{IAM_BASE_URL}/auth/admin/realms/{TENANT_NAME}/clients"


def parse_client_id_from_location(location: str) -> str | None:
    match = re.search(r"/clients/([^/]+)$", location)
    return match.group(1) if match else None


def get_iam_client_by_client_id(client_id: str) -> dict | None:
    response = request_with_refresh(
        "GET",
        build_iam_clients_url(),
        headers={"Accept": "application/json"},
        params={"clientId": client_id},
    )
    if response.status_code != 200:
        print(f"Lookup OAuth client failed: {response.status_code}")
        print(response.text)
        response.raise_for_status()
    clients = response.json()
    return clients[0] if clients else None


def create_iam_oauth_client(client_id: str) -> dict:
    existing = get_iam_client_by_client_id(client_id)
    if existing:
        print(f"OAuth client already exists: {client_id}")
        return existing

    response = request_with_refresh(
        "POST",
        build_iam_clients_url(),
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
        print(f"Create OAuth client failed: {response.status_code}")
        print(response.text)
        response.raise_for_status()

    created = get_iam_client_by_client_id(client_id)
    if not created:
        location = response.headers.get("Location", "")
        internal_id = parse_client_id_from_location(location)
        if not internal_id:
            raise ValueError(f"OAuth client {client_id} created but id not found.")
        created = {"id": internal_id, "clientId": client_id}
    print(f"OAuth client ready: {client_id}")
    return created


def get_iam_client_by_internal_id(client_internal_id: str) -> dict:
    response = request_with_refresh(
        "GET",
        f"{build_iam_clients_url()}/{client_internal_id}",
        headers={"Accept": "application/json"},
    )
    if response.status_code != 200:
        print(f"Get OAuth client failed: {response.status_code}")
        print(response.text)
        response.raise_for_status()
    return response.json()


def ensure_service_account_enabled(client_internal_id: str) -> None:
    client = get_iam_client_by_internal_id(client_internal_id)
    if client.get("serviceAccountsEnabled"):
        return

    client["serviceAccountsEnabled"] = True
    response = request_with_refresh(
        "PUT",
        f"{build_iam_clients_url()}/{client_internal_id}",
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
        json=client,
    )
    if response.status_code not in (200, 204):
        print(f"Enable service account failed: {response.status_code}")
        print(response.text)
        response.raise_for_status()


def get_group_id_by_name(groups: list[dict], group_name: str) -> str:
    for group in groups:
        if group.get("name") == group_name:
            group_id = group.get("id")
            if group_id:
                return group_id
    raise ValueError(f"Group '{group_name}' not found in tenant.")


def add_oauth_client_to_groups(
    client_internal_id: str, group_assignments: list[tuple[str, str]]
) -> None:
    ensure_service_account_enabled(client_internal_id)

    sa_response = request_with_refresh(
        "GET",
        f"{build_iam_clients_url()}/{client_internal_id}/service-account-user",
        headers={"Accept": "application/json"},
    )
    if sa_response.status_code != 200:
        print(f"Get service account user failed: {sa_response.status_code}")
        print(sa_response.text)
        sa_response.raise_for_status()
    service_account_user_id = sa_response.json().get("id", "")
    if not service_account_user_id:
        raise ValueError("Service account user id is empty.")

    for group_id, group_name in group_assignments:
        group_response = request_with_refresh(
            "PUT",
            (
                f"https://{IAM_BASE_URL}/auth/admin/realms/{TENANT_NAME}/users/"
                f"{service_account_user_id}/groups/{group_id}"
            ),
            headers={"Accept": "application/json"},
        )
        if group_response.status_code not in (200, 204):
            print(f"Add OAuth client to {group_name} failed: {group_response.status_code}")
            print(group_response.text)
            group_response.raise_for_status()
        print(f"OAuth client service account added to {group_name}")


def get_iam_client_secret(client_internal_id: str) -> str:
    url = f"{build_iam_clients_url()}/{client_internal_id}/client-secret"
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
        print(f"Get OAuth client secret failed: {response.status_code}")
        print(response.text)
        response.raise_for_status()

    secret = response.json().get("value", "")
    if not secret:
        raise ValueError("OAuth client secret response did not include a value.")
    return secret


def ensure_oauth_client_group_assignments(group_map: dict, groups: list[dict]) -> dict:
    lead_group_name = f"CHMX_LEAD_{COD_APP}_PROD"
    lead_group_id = group_map.get(lead_group_name)
    if not lead_group_id:
        raise ValueError(f"Missing group id for {lead_group_name}")

    plugin_scanner_group_id = get_group_id_by_name(groups, PLUGIN_SCANNER_GROUP)

    client = create_iam_oauth_client(COD_APP)
    client_internal_id = client.get("id", "")
    if not client_internal_id:
        raise ValueError("OAuth client internal id is missing.")

    add_oauth_client_to_groups(
        client_internal_id,
        [
            (lead_group_id, lead_group_name),
            (plugin_scanner_group_id, PLUGIN_SCANNER_GROUP),
        ],
    )
    print(
        f"OAuth client inherits plugin_scanner via group {PLUGIN_SCANNER_GROUP} "
        "(Phase 1 workaround)."
    )

    client_secret = get_iam_client_secret(client_internal_id)
    print("OAuth client secret retrieved and stored in oauth_client_info.json")

    info = {
        "clientId": COD_APP,
        "iamClientId": client_internal_id,
        "clientSecret": client_secret,
        "leadGroupName": lead_group_name,
        "leadGroupId": lead_group_id,
        "pluginScannerGroupName": PLUGIN_SCANNER_GROUP,
        "pluginScannerGroupId": plugin_scanner_group_id,
        "roleViaGroup": "plugin_scanner",
    }
    write_json(OAUTH_CLIENT_INFO_FILE, info)
    print(f"Stored OAuth client info in {OAUTH_CLIENT_INFO_FILE}")
    return info


def assign_group_base_roles(group_map: dict) -> None:
    dev_group_name = f"CHMX_DEV_{COD_APP}_PROD"
    lead_group_name = f"CHMX_LEAD_{COD_APP}_PROD"
    if assign_base_role(
        group_map[dev_group_name], "chmx_dev_base_role", entity_label=dev_group_name
    ):
        print(f"Assigned chmx_dev_base_role to {dev_group_name}")
    if assign_base_role(
        group_map[lead_group_name], "chmx_lead_base_role", entity_label=lead_group_name
    ):
        print(f"Assigned chmx_lead_base_role to {lead_group_name}")


def assign_base_role(entity_id: str, role_name: str, entity_label: str = "") -> bool:
    label = entity_label or entity_id
    response = request_with_refresh(
        "POST",
        f"https://{AST_BASE_URL}/api/access-management/base-roles/{entity_id}",
        headers={
            "Accept": "application/json",
            "Content-Type": "application/json",
        },
        json={"baseRoles": [role_name]},
    )
    if response.status_code in (200, 201, 204):
        return True

    print(f"Assign base role failed for {label} ({role_name}): {response.status_code}")
    print(response.text)
    if CONTINUE_ON_ROLE_ASSIGNMENT_ERROR:
        print(
            "Skipping role assignment and continuing "
            "(CONTINUE_ON_ROLE_ASSIGNMENT_ERROR=true)."
        )
        return False
    response.raise_for_status()
    return False


def ensure_application() -> dict:
    apps = list_all_applications(page_size=100)
    existing = next((a for a in apps if a.get("name") == COD_APP), None)
    if existing:
        print(f"Application already exists: {COD_APP}")
        app_info = {"id": existing.get("id"), "name": existing.get("name"), "created_now": False}
        write_json(APPLICATION_INFO_FILE, app_info)
        return app_info

    created = create_application()
    app_info = {"id": created.get("id"), "name": created.get("name"), "created_now": True}
    write_json(APPLICATION_INFO_FILE, app_info)
    print(f"Application created: {COD_APP}")
    return app_info


def list_all_applications(page_size: int = 100) -> list[dict]:
    apps: list[dict] = []
    offset = 0
    while True:
        response = request_with_refresh(
            "GET",
            f"https://{AST_BASE_URL}/api/applications/",
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


def create_application() -> dict:
    response = request_with_refresh(
        "POST",
        f"https://{AST_BASE_URL}/api/applications/",
        headers={
            "Accept": "application/json; version=1.0",
            "Content-Type": "application/json; version=1.0",
            "CorrelationId": str(uuid.uuid4()),
        },
        json={
            "name": COD_APP,
            "description": "",
            "criticality": 3,
            "rules": [{"type": "project.tag.key.exists", "value": COD_APP}],
            "tags": {},
        },
    )
    if response.status_code not in (200, 201):
        print(f"Create application failed: {response.status_code}")
        print(response.text)
        response.raise_for_status()
    return response.json()


def assign_groups_to_application(group_map: dict, app_info: dict) -> None:
    app_id = app_info.get("id")
    if not app_id:
        raise ValueError("Application id is missing.")
    for group_name in [f"CHMX_DEV_{COD_APP}_PROD", f"CHMX_LEAD_{COD_APP}_PROD"]:
        create_assignment(group_map[group_name], app_id)
        print(f"Assigned {group_name} to application")


def create_assignment(group_id: str, app_id: str) -> None:
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
            "resourceID": app_id,
            "resourceType": "application",
        },
    )
    if response.status_code not in (200, 201, 204):
        print(f"Create assignment failed: {response.status_code}")
        print(response.text)
        response.raise_for_status()


def main() -> None:
    validate_inputs()
    fetch_access_token()
    print("Token fetched and cached.")

    groups = list_groups()
    group_map = ensure_groups_from_list(groups)
    assign_group_base_roles(group_map)
    ensure_oauth_client_group_assignments(group_map, groups)

    app_info = ensure_application()
    print(f"Stored application info in {APPLICATION_INFO_FILE}")

    # In restricted tenants this may fail with 403 forbidden action.
    assign_groups_to_application(group_map, app_info)
    print("Flow completed successfully.")


if __name__ == "__main__":
    main()
