import json
import os
from datetime import datetime, timedelta, timezone

import requests
from dotenv import load_dotenv

load_dotenv()

tenant_name = os.getenv("TENANT_NAME", "")
oauth_client = os.getenv("OAUTH_CLIENT", "")
secret_key = os.getenv("SECRET_KEY", "")
iam_base_url = os.getenv("IAM_BASE_URL", "iam.checkmarx.net")
token_cache_file = "token_cache.json"


def validate_inputs() -> None:
    if not tenant_name or not oauth_client or not secret_key:
        raise ValueError(
            "Missing env vars. Set TENANT_NAME, OAUTH_CLIENT, and SECRET_KEY in .env"
        )


def build_token_url() -> str:
    return (
        f"https://{iam_base_url}/auth/realms/"
        f"{tenant_name}/protocol/openid-connect/token"
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
        "access_token_expires_at": (
            now + timedelta(seconds=expires_in)
        ).isoformat(),
        "refresh_token_expires_at": (
            now + timedelta(seconds=refresh_expires_in)
        ).isoformat()
        if refresh_expires_in > 0
        else None,
        "saved_at": now.isoformat(),
    }

    with open(token_cache_file, "w", encoding="utf-8") as file:
        json.dump(cache_data, file, indent=2)


def load_cached_tokens() -> dict:
    if not os.path.exists(token_cache_file):
        raise FileNotFoundError(
            f"Token cache not found: {token_cache_file}. Run get_token.py first."
        )

    with open(token_cache_file, "r", encoding="utf-8") as file:
        return json.load(file)


def get_authorization_header() -> dict:
    token_data = load_cached_tokens()
    access_token = token_data.get("access_token", "")
    token_type = token_data.get("token_type", "bearer")

    if not access_token:
        raise ValueError("Cached token file exists but access_token is empty.")

    return {"Authorization": f"{token_type.capitalize()} {access_token}"}


def refresh_access_token() -> dict:
    validate_inputs()

    cached_tokens = load_cached_tokens()
    refresh_token = cached_tokens.get("refresh_token", "")

    if not refresh_token:
        raise ValueError(
            "No refresh_token found in token_cache.json. Run get_token.py first."
        )

    url = build_token_url()
    headers = {
        "Content-Type": "application/x-www-form-urlencoded",
        "Accept": "application/json",
    }
    data = {
        "client_id": oauth_client,
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
        "client_secret": secret_key,
    }

    response = requests.post(url, headers=headers, data=data, timeout=30)

    if response.status_code != 200:
        print(f"Status: {response.status_code}")
        print(response.text)
        response.raise_for_status()

    payload = response.json()
    save_tokens(payload)
    return payload


def main() -> None:
    validate_inputs()

    url = build_token_url()

    headers = {
        "Content-Type": "application/x-www-form-urlencoded",
        "Accept": "application/json",
    }

    data = {
        "client_id": oauth_client,
        "grant_type": "client_credentials",
        "client_secret": secret_key,
    }

    response = requests.post(url, headers=headers, data=data, timeout=30)

    if response.status_code != 200:
        print(f"Status: {response.status_code}")
        print(response.text)
        response.raise_for_status()

    payload = response.json()
    save_tokens(payload)

    print("Status: 200")
    print("Tokens saved to token_cache.json")
    print(f"access_token expires in: {payload.get('expires_in')} seconds")
    print(f"refresh_token expires in: {payload.get('refresh_expires_in')} seconds")


if __name__ == "__main__":
    main()
