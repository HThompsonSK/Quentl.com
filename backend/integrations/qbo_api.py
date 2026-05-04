import os
from typing import Any, Dict, List
from urllib.parse import urlencode

import httpx

QBO_AUTH = "https://appcenter.intuit.com/connect/oauth2"
QBO_TOKEN = "https://oauth.platform.intuit.com/oauth2/v1/tokens/bearer"


def _qbo_base_api() -> str:
    env = (os.getenv("QBO_ENVIRONMENT") or "sandbox").lower().strip()
    if env == "production":
        return "https://quickbooks.api.intuit.com"
    return "https://sandbox-quickbooks.api.intuit.com"


def qbo_redirect_uri() -> str:
    base = os.getenv("APP_BASE_URL", "http://127.0.0.1:9090").rstrip("/")
    return f"{base}/api/integrations/quickbooks/callback"


def build_authorize_url(state: str) -> str:
    client_id = os.getenv("QUICKBOOKS_CLIENT_ID", "")
    params = {
        "client_id": client_id,
        "response_type": "code",
        "scope": "com.intuit.quickbooks.accounting",
        "redirect_uri": qbo_redirect_uri(),
        "state": state,
    }
    return f"{QBO_AUTH}?{urlencode(params)}"


def exchange_code(code: str) -> Dict[str, Any]:
    client_id = os.getenv("QUICKBOOKS_CLIENT_ID", "")
    client_secret = os.getenv("QUICKBOOKS_CLIENT_SECRET", "")
    auth = httpx.BasicAuth(client_id, client_secret)
    data = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": qbo_redirect_uri(),
    }
    with httpx.Client(timeout=30.0) as client:
        r = client.post(QBO_TOKEN, data=data, auth=auth)
        r.raise_for_status()
        return r.json()


def refresh_access(refresh_token: str) -> Dict[str, Any]:
    client_id = os.getenv("QUICKBOOKS_CLIENT_ID", "")
    client_secret = os.getenv("QUICKBOOKS_CLIENT_SECRET", "")
    auth = httpx.BasicAuth(client_id, client_secret)
    data = {"grant_type": "refresh_token", "refresh_token": refresh_token}
    with httpx.Client(timeout=30.0) as client:
        r = client.post(QBO_TOKEN, data=data, auth=auth)
        r.raise_for_status()
        return r.json()


def _query(client: httpx.Client, realm_id: str, access_token: str, q: str) -> List[Dict[str, Any]]:
    url = f"{_qbo_base_api()}/v3/company/{realm_id}/query"
    r = client.get(
        url,
        params={"query": q, "minorversion": "65"},
        headers={"Authorization": f"Bearer {access_token}", "Accept": "application/json"},
    )
    r.raise_for_status()
    data = r.json()
    qres = data.get("QueryResponse") or {}
    for key in ("Invoice", "Payment"):
        if key in qres:
            rows = qres[key]
            return rows if isinstance(rows, list) else [rows]
    return []


def fetch_invoices(access_token: str, realm_id: str) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    start = 1
    with httpx.Client(timeout=60.0) as client:
        while True:
            q = f"select * from Invoice STARTPOSITION {start} MAXRESULTS 100"
            batch = _query(client, realm_id, access_token, q)
            if not batch:
                break
            out.extend(batch)
            if len(batch) < 100:
                break
            start += len(batch)
    return out


def fetch_payments(access_token: str, realm_id: str) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    start = 1
    with httpx.Client(timeout=60.0) as client:
        while True:
            q = f"select * from Payment STARTPOSITION {start} MAXRESULTS 100"
            batch = _query(client, realm_id, access_token, q)
            if not batch:
                break
            out.extend(batch)
            if len(batch) < 100:
                break
            start += len(batch)
    return out
