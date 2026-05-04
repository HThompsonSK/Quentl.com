import os
from typing import Any, Dict, List, Optional
from urllib.parse import urlencode

import httpx

XERO_AUTH = "https://login.xero.com/identity/connect/authorize"
XERO_TOKEN = "https://identity.xero.com/connect/token"
XERO_API = "https://api.xero.com"


def xero_redirect_uri() -> str:
    base = os.getenv("APP_BASE_URL", "http://127.0.0.1:9090").rstrip("/")
    return f"{base}/api/integrations/xero/callback"


def build_authorize_url(state: str) -> str:
    client_id = os.getenv("XERO_CLIENT_ID", "")
    params = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": xero_redirect_uri(),
        "scope": "offline_access accounting.transactions.read accounting.contacts.read",
        "state": state,
    }
    return f"{XERO_AUTH}?{urlencode(params)}"


def exchange_code(code: str) -> Dict[str, Any]:
    client_id = os.getenv("XERO_CLIENT_ID", "")
    client_secret = os.getenv("XERO_CLIENT_SECRET", "")
    data = {
        "grant_type": "authorization_code",
        "client_id": client_id,
        "client_secret": client_secret,
        "code": code,
        "redirect_uri": xero_redirect_uri(),
    }
    with httpx.Client(timeout=30.0) as client:
        r = client.post(
            XERO_TOKEN,
            data=data,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        r.raise_for_status()
        return r.json()


def refresh_access(refresh_token: str) -> Dict[str, Any]:
    client_id = os.getenv("XERO_CLIENT_ID", "")
    client_secret = os.getenv("XERO_CLIENT_SECRET", "")
    data = {
        "grant_type": "refresh_token",
        "client_id": client_id,
        "client_secret": client_secret,
        "refresh_token": refresh_token,
    }
    with httpx.Client(timeout=30.0) as client:
        r = client.post(
            XERO_TOKEN,
            data=data,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        r.raise_for_status()
        return r.json()


def fetch_connections(access_token: str) -> List[Dict[str, Any]]:
    with httpx.Client(timeout=30.0) as client:
        r = client.get(
            f"{XERO_API}/connections",
            headers={
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/json",
            },
        )
        r.raise_for_status()
        return r.json()


def _xero_headers(access_token: str, tenant_id: str) -> Dict[str, str]:
    return {
        "Authorization": f"Bearer {access_token}",
        "Xero-tenant-id": tenant_id,
        "Accept": "application/json",
    }


def fetch_all_invoices(access_token: str, tenant_id: str) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    page = 1
    with httpx.Client(timeout=60.0) as client:
        while True:
            r = client.get(
                f"{XERO_API}/api.xro/2.0/Invoices",
                params={"page": page},
                headers=_xero_headers(access_token, tenant_id),
            )
            r.raise_for_status()
            body = r.json()
            batch = body.get("Invoices") or []
            if not batch:
                break
            out.extend(batch)
            if len(batch) < 100:
                break
            page += 1
    return out


def fetch_all_payments(access_token: str, tenant_id: str) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    page = 1
    with httpx.Client(timeout=60.0) as client:
        while True:
            r = client.get(
                f"{XERO_API}/api.xro/2.0/Payments",
                params={"page": page},
                headers=_xero_headers(access_token, tenant_id),
            )
            r.raise_for_status()
            body = r.json()
            batch = body.get("Payments") or []
            if not batch:
                break
            out.extend(batch)
            if len(batch) < 100:
                break
            page += 1
    return out
