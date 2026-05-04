import os
from typing import Any, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import RedirectResponse
from pydantic import BaseModel

from db import get_db
from integrations import qbo_api, xero_api
from integrations.oauth_state import sign_oauth_state, verify_oauth_state
from integrations.sync_service import (
    save_oauth_connection,
    sync_quickbooks_for_company,
    sync_xero_for_company,
)

router = APIRouter(prefix="/api/integrations", tags=["integrations"])
actuals_router = APIRouter(prefix="/api/actuals", tags=["actuals"])


def _frontend_base() -> str:
    return os.getenv("APP_BASE_URL", "http://127.0.0.1:9090").rstrip("/")


class CompanyBody(BaseModel):
    company_id: int


class ActualLineLeadPatch(BaseModel):
    company_id: int
    lead_id: Optional[int] = None


class LeadAccountingLinkCreate(BaseModel):
    company_id: int
    lead_id: int
    provider: str
    external_contact_id: str


@router.get("/xero/start")
def xero_oauth_start(company_id: int = Query(..., ge=1)):
    if not os.getenv("XERO_CLIENT_ID"):
        raise HTTPException(503, "XERO_CLIENT_ID is not configured.")
    state = sign_oauth_state(company_id, "xero")
    return RedirectResponse(xero_api.build_authorize_url(state))


@router.get("/xero/callback")
def xero_oauth_callback(code: str, state: str, conn=Depends(get_db)):
    parsed = verify_oauth_state(state)
    if not parsed:
        raise HTTPException(400, "Invalid or expired OAuth state.")
    company_id, _ = parsed
    try:
        tokens = xero_api.exchange_code(code)
        access = tokens["access_token"]
        refresh = tokens["refresh_token"]
        expires_in = int(tokens.get("expires_in", 1800))
        conns = xero_api.fetch_connections(access)
        if not conns:
            raise HTTPException(400, "No Xero organisations returned for this user.")
        tenant_id = conns[0].get("tenantId")
        if not tenant_id:
            raise HTTPException(400, "Missing tenantId from Xero connections.")
        with conn.cursor() as cur:
            save_oauth_connection(
                cur,
                company_id,
                "xero",
                access,
                refresh,
                expires_in,
                tenant_id=str(tenant_id),
                realm_id=None,
            )
            conn.commit()
    except HTTPException:
        raise
    except Exception as e:
        conn.rollback()
        raise HTTPException(502, f"Xero token exchange failed: {e}") from e
    return RedirectResponse(f"{_frontend_base()}/settings.html?integration=xero_ok")


@router.get("/quickbooks/start")
def qbo_oauth_start(company_id: int = Query(..., ge=1)):
    if not os.getenv("QUICKBOOKS_CLIENT_ID"):
        raise HTTPException(503, "QUICKBOOKS_CLIENT_ID is not configured.")
    state = sign_oauth_state(company_id, "quickbooks")
    return RedirectResponse(qbo_api.build_authorize_url(state))


@router.get("/quickbooks/callback")
def qbo_oauth_callback(
    code: str,
    state: str,
    realm_id: str = Query(..., alias="realmId"),
    conn=Depends(get_db),
):
    parsed = verify_oauth_state(state)
    if not parsed:
        raise HTTPException(400, "Invalid or expired OAuth state.")
    company_id, _ = parsed
    try:
        tokens = qbo_api.exchange_code(code)
        access = tokens["access_token"]
        refresh = tokens["refresh_token"]
        expires_in = int(tokens.get("expires_in", 3600))
        with conn.cursor() as cur:
            save_oauth_connection(
                cur,
                company_id,
                "quickbooks",
                access,
                refresh,
                expires_in,
                tenant_id=None,
                realm_id=str(realm_id),
            )
            conn.commit()
    except HTTPException:
        raise
    except Exception as e:
        conn.rollback()
        raise HTTPException(502, f"QuickBooks token exchange failed: {e}") from e
    return RedirectResponse(f"{_frontend_base()}/settings.html?integration=qbo_ok")


@router.get("/status/{company_id}")
def integration_status(company_id: int, conn=Depends(get_db)):
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT id, service_name, tenant_id, realm_id, expires_at
            FROM api_connections WHERE company_id = %s
            """,
            (company_id,),
        )
        rows = cur.fetchall()
    out = []
    for r in rows:
        out.append(
            {
                "service_name": r["service_name"],
                "connected": True,
                "xero_tenant_id": r["tenant_id"],
                "quickbooks_realm_id": r["realm_id"],
                "token_expires_at": r["expires_at"].isoformat() if r["expires_at"] else None,
            }
        )
    return {"company_id": company_id, "connections": out}


@router.post("/xero/sync")
def xero_sync(body: CompanyBody, conn=Depends(get_db)):
    try:
        with conn.cursor() as cur:
            result = sync_xero_for_company(cur, body.company_id)
            conn.commit()
        return result
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    except Exception as e:
        conn.rollback()
        raise HTTPException(500, str(e)) from e


@router.post("/quickbooks/sync")
def qbo_sync(body: CompanyBody, conn=Depends(get_db)):
    try:
        with conn.cursor() as cur:
            result = sync_quickbooks_for_company(cur, body.company_id)
            conn.commit()
        return result
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    except Exception as e:
        conn.rollback()
        raise HTTPException(500, str(e)) from e


@router.post("/lead-links")
def create_lead_link(item: LeadAccountingLinkCreate, conn=Depends(get_db)):
    p = item.provider.lower().strip()
    if p not in ("xero", "quickbooks"):
        raise HTTPException(400, "provider must be 'xero' or 'quickbooks'")
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id FROM leads WHERE id = %s AND company_id = %s",
                (item.lead_id, item.company_id),
            )
            if not cur.fetchone():
                raise HTTPException(400, "Lead not found for this company.")
            cur.execute(
                """
                INSERT INTO lead_accounting_links (company_id, lead_id, provider, external_contact_id)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (company_id, provider, external_contact_id) DO UPDATE SET
                    lead_id = EXCLUDED.lead_id
                RETURNING *
                """,
                (item.company_id, item.lead_id, p, item.external_contact_id.strip()),
            )
            row = cur.fetchone()
            conn.commit()
        return row
    except HTTPException:
        conn.rollback()
        raise
    except Exception as e:
        conn.rollback()
        raise HTTPException(500, str(e)) from e


@router.delete("/lead-links/{link_id}")
def delete_lead_link(link_id: int, conn=Depends(get_db)):
    with conn.cursor() as cur:
        cur.execute("DELETE FROM lead_accounting_links WHERE id = %s RETURNING id", (link_id,))
        row = cur.fetchone()
        conn.commit()
    if not row:
        raise HTTPException(404, "Link not found")
    return {"success": True}


@router.get("/lead-links/{company_id}")
def list_lead_links(company_id: int, conn=Depends(get_db)):
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT l.*, le.client_name
            FROM lead_accounting_links l
            JOIN leads le ON le.id = l.lead_id
            WHERE l.company_id = %s
            ORDER BY l.id DESC
            """,
            (company_id,),
        )
        return cur.fetchall()


@actuals_router.get("/summary/{company_id}")
def actuals_summary(company_id: int, conn=Depends(get_db)):
    legend = {
        "accrual": (
            "Invoice totals by invoice date from your accounting system "
            "(revenue recognition when the invoice is recorded)."
        ),
        "cash": (
            "Customer payment amounts by payment date (cash received). "
            "Do not merge uncorrelated bank deposits with these rows or you may double-count."
        ),
    }
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT
                date_trunc('month', posted_date)::date AS month,
                basis,
                SUM(amount) AS total_amount,
                currency
            FROM accounting_actual_lines
            WHERE company_id = %s
            GROUP BY date_trunc('month', posted_date)::date, basis, currency
            ORDER BY month DESC, basis
            """,
            (company_id,),
        )
        rows = cur.fetchall()
    return {"company_id": company_id, "legend": legend, "by_month": rows}


@actuals_router.get("/lines/{company_id}")
def actuals_lines(
    company_id: int,
    basis: Optional[str] = Query(None, description="accrual or cash"),
    limit: int = Query(200, ge=1, le=2000),
    conn=Depends(get_db),
):
    q = """
        SELECT a.*, le.client_name AS linked_lead_name
        FROM accounting_actual_lines a
        LEFT JOIN leads le ON le.id = a.lead_id
        WHERE a.company_id = %s
    """
    params: List[Any] = [company_id]
    if basis in ("accrual", "cash"):
        q += " AND a.basis = %s"
        params.append(basis)
    q += " ORDER BY a.posted_date DESC, a.id DESC LIMIT %s"
    params.append(limit)
    with conn.cursor() as cur:
        cur.execute(q, tuple(params))
        return cur.fetchall()


@actuals_router.patch("/lines/{line_id}")
def patch_actual_line(line_id: int, body: ActualLineLeadPatch, conn=Depends(get_db)):
    try:
        with conn.cursor() as cur:
            if body.lead_id is not None:
                cur.execute(
                    "SELECT id FROM leads WHERE id = %s AND company_id = %s",
                    (body.lead_id, body.company_id),
                )
                if not cur.fetchone():
                    raise HTTPException(400, "Lead not found for this company.")
            cur.execute(
                """
                UPDATE accounting_actual_lines SET lead_id = %s
                WHERE id = %s AND company_id = %s
                RETURNING *
                """,
                (body.lead_id, line_id, body.company_id),
            )
            row = cur.fetchone()
            if not row:
                raise HTTPException(404, "Line not found for this company.")
            conn.commit()
        return row
    except HTTPException:
        conn.rollback()
        raise


def register_integration_routes(app):
    app.include_router(router)
    app.include_router(actuals_router)
