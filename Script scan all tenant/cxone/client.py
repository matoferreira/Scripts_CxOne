import os
import time
from dataclasses import dataclass
from typing import Optional
from urllib.parse import urljoin

import requests

REGION_IAM = {
    "US": "iam.checkmarx.net",
    "US2": "us.iam.checkmarx.net",
    "EU": "eu.iam.checkmarx.net",
    "EU2": "eu-2.iam.checkmarx.net",
    "DEU": "deu.iam.checkmarx.net",
    "ANZ": "anz.iam.checkmarx.net",
    "India": "ind.iam.checkmarx.net",
    "Singapore": "sng.iam.checkmarx.net",
    "UAE": "mea.iam.checkmarx.net",
}

REGION_API = {
    "US": "ast.checkmarx.net",
    "US2": "us.ast.checkmarx.net",
    "EU": "eu.ast.checkmarx.net",
    "EU2": "eu-2.ast.checkmarx.net",
    "DEU": "deu.ast.checkmarx.net",
    "ANZ": "anz.ast.checkmarx.net",
    "India": "ind.ast.checkmarx.net",
    "Singapore": "sng.ast.checkmarx.net",
    "UAE": "mea.ast.checkmarx.net",
}


@dataclass
class Settings:
    tenant: str
    iam_host: str
    api_host: str
    api_key: Optional[str]
    client_id: Optional[str]
    client_secret: Optional[str]
    max_concurrent: int
    submit_workers: int
    scan_tag: str
    scan_engines: list[str] | None = None
    agent_name: str = "bulk-scan-script"


def load_settings() -> Settings:
    tenant = os.environ["CX_TENANT"]
    region = os.getenv("CX_REGION", "US2")
    iam_host = os.getenv("CX_IAM_HOST") or REGION_IAM.get(region)
    api_host = os.getenv("CX_API_HOST") or REGION_API.get(region)
    if not iam_host or not api_host:
        raise ValueError(f"Unknown CX_REGION '{region}'. Set CX_IAM_HOST and CX_API_HOST.")

    return Settings(
        tenant=tenant,
        iam_host=iam_host,
        api_host=api_host,
        api_key=os.getenv("CX_API_KEY") or None,
        client_id=os.getenv("CX_CLIENT_ID") or None,
        client_secret=os.getenv("CX_CLIENT_SECRET") or None,
        max_concurrent=int(os.getenv("CX_MAX_CONCURRENT", "800")),
        submit_workers=int(os.getenv("CX_SUBMIT_WORKERS", "50")),
        scan_tag=os.getenv("CX_SCAN_TAG", "bulk-scan"),
        scan_engines=_parse_scan_engines(os.getenv("CX_SCAN_ENGINES")),
    )


def _parse_scan_engines(raw: str | None) -> list[str] | None:
    if not raw or not raw.strip():
        return None
    engines = [part.strip().lower() for part in raw.split(",") if part.strip()]
    return engines or None


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
        s = self.settings
        if s.api_key:
            data = {
                "grant_type": "refresh_token",
                "client_id": "ast-app",
                "refresh_token": s.api_key,
            }
        elif s.client_id and s.client_secret:
            data = {
                "grant_type": "client_credentials",
                "client_id": s.client_id,
                "client_secret": s.client_secret,
            }
        else:
            raise ValueError("Set CX_API_KEY or CX_CLIENT_ID + CX_CLIENT_SECRET")

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

    def _headers(self) -> dict:
        if not self._token or time.time() >= self._token_expires_at:
            self._authenticate()
        return {
            "Authorization": f"Bearer {self._token}",
            "Accept": "*/*; version=1.0",
            "Content-Type": "application/json",
            "User-Agent": self.settings.agent_name,
        }

    def request(self, method: str, path: str, **kwargs) -> requests.Response:
        url = urljoin(self.api_base, path.lstrip("/"))
        headers = kwargs.pop("headers", {})
        merged = {**self._headers(), **headers}

        resp = None
        for attempt in range(5):
            try:
                resp = requests.request(
                    method, url, headers=merged, timeout=self.timeout, **kwargs
                )
            except requests.RequestException as exc:
                if attempt < 4:
                    time.sleep(min(2**attempt, 30))
                    continue
                raise exc
            if resp.status_code == 401:
                self._authenticate()
                merged = {**self._headers(), **headers}
                continue
            if resp.status_code in (429, 503) and attempt < 4:
                time.sleep(min(2**attempt, 30))
                continue
            return resp
        return resp

    def get(self, path: str, **kwargs) -> requests.Response:
        return self.request("GET", path, **kwargs)

    def post(self, path: str, **kwargs) -> requests.Response:
        return self.request("POST", path, **kwargs)
