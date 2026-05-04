from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Dict, Optional, Tuple

from integrations import qbo_api, xero_api
from integrations.token_store import decrypt_token, encrypt_token
from integrations.xero_dates import parse_xero_date


def _utcnow():
    return datetime.now(timezone.utc)


def _token_expired(expires_at) -> bool:
    if expires_at is None:
        return True
    now = _utcnow()
    if isinstance(expires_at, datetime):
        exp = expires_at
        if exp.tzinfo is None:
            exp = exp.replace(tzinfo=timezone.utc)
        return now >= exp - timedelta(minutes=2)
    return True


def _get_connection(cur, company_id: int, service_name: str) -> Optional[Dict[str, Any]]:
    cur.execute(
        """
        SELECT * FROM api_connections
        WHERE company_id = %s AND service_name = %s
        LIMIT 1
        """,
        (company_id, service_name),
    )
    return cur.fetchone()


def save_oauth_connection(
    cur,
    company_id: int,
    service_name: str,
    access_token: str,
    refresh_token: str,
    expires_in_seconds: int,
    tenant_id: Optional[str] = None,
    realm_id: Optional[str] = None,
):
    exp = _utcnow() + timedelta(seconds=max(60, int(expires_in_seconds)))
    return _save_tokens(
        cur,
        company_id,
        service_name,
        access_token,
        refresh_token,
        exp,
        tenant_id=tenant_id,
        realm_id=realm_id,
    )


def _save_tokens(
    cur,
    company_id: int,
    service_name: str,
    access_token: str,
    refresh_token: str,
    expires_at: Optional[datetime],
    tenant_id: Optional[str] = None,
    realm_id: Optional[str] = None,
):
    cur.execute(
        """
        INSERT INTO api_connections (
            company_id, service_name, access_token, refresh_token,
            expires_at, tenant_id, realm_id
        ) VALUES (%s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (company_id, service_name) DO UPDATE SET
            access_token = EXCLUDED.access_token,
            refresh_token = EXCLUDED.refresh_token,
            expires_at = EXCLUDED.expires_at,
            tenant_id = COALESCE(EXCLUDED.tenant_id, api_connections.tenant_id),
            realm_id = COALESCE(EXCLUDED.realm_id, api_connections.realm_id)
        RETURNING *
        """,
        (
            company_id,
            service_name,
            encrypt_token(access_token),
            encrypt_token(refresh_token),
            expires_at,
            tenant_id,
            realm_id,
        ),
    )
    return cur.fetchone()


def ensure_xero_access(cur, company_id: int) -> Tuple[str, str]:
    row = _get_connection(cur, company_id, "xero")
    if not row:
        raise ValueError("No Xero connection for this company.")
    access = decrypt_token(row["access_token"])
    refresh = decrypt_token(row["refresh_token"])
    tenant_id = row["tenant_id"]
    if not tenant_id:
        raise ValueError("Missing Xero tenant_id; reconnect Xero.")
    if _token_expired(row["expires_at"]):
        data = xero_api.refresh_access(refresh)
        access = data["access_token"]
        refresh = data.get("refresh_token", refresh)
        expires_in = int(data.get("expires_in", 1800))
        exp = _utcnow() + timedelta(seconds=expires_in)
        _save_tokens(cur, company_id, "xero", access, refresh, exp, tenant_id=tenant_id)
    return access, tenant_id


def ensure_qbo_access(cur, company_id: int) -> Tuple[str, str]:
    row = _get_connection(cur, company_id, "quickbooks")
    if not row:
        raise ValueError("No QuickBooks connection for this company.")
    access = decrypt_token(row["access_token"])
    refresh = decrypt_token(row["refresh_token"])
    realm_id = row.get("realm_id")
    if not realm_id:
        raise ValueError("Missing QuickBooks realm_id; reconnect QuickBooks.")
    if _token_expired(row["expires_at"]):
        data = qbo_api.refresh_access(refresh)
        access = data["access_token"]
        refresh = data.get("refresh_token", refresh)
        expires_in = int(data.get("expires_in", 3600))
        exp = _utcnow() + timedelta(seconds=expires_in)
        _save_tokens(cur, company_id, "quickbooks", access, refresh, exp, realm_id=realm_id)
    return access, realm_id


def _resolve_lead(cur, company_id: int, provider: str, contact_id: Optional[str]) -> Optional[int]:
    if not contact_id:
        return None
    cur.execute(
        """
        SELECT lead_id FROM lead_accounting_links
        WHERE company_id = %s AND provider = %s AND external_contact_id = %s
        LIMIT 1
        """,
        (company_id, provider, str(contact_id)),
    )
    r = cur.fetchone()
    return int(r["lead_id"]) if r else None


def _upsert_line(
    cur,
    company_id: int,
    connection_id: Optional[int],
    provider: str,
    basis: str,
    line_kind: str,
    *,
    external_invoice_id: Optional[str],
    external_payment_id: Optional[str],
    external_contact_id: Optional[str],
    contact_name: Optional[str],
    amount: Decimal,
    currency: str,
    posted_date: date,
    idempotency_key: str,
    lead_id: Optional[int],
):
    cur.execute(
        """
        INSERT INTO accounting_actual_lines (
            company_id, connection_id, provider, basis, line_kind,
            external_invoice_id, external_payment_id, external_contact_id,
            contact_name, amount, currency, posted_date, lead_id, idempotency_key
        ) VALUES (
            %s, %s, %s, %s, %s,
            %s, %s, %s, %s, %s, %s, %s, %s, %s
        )
        ON CONFLICT (company_id, idempotency_key) DO UPDATE SET
            amount = EXCLUDED.amount,
            posted_date = EXCLUDED.posted_date,
            contact_name = EXCLUDED.contact_name,
            currency = EXCLUDED.currency,
            external_invoice_id = EXCLUDED.external_invoice_id,
            external_payment_id = EXCLUDED.external_payment_id,
            external_contact_id = EXCLUDED.external_contact_id,
            synced_at = CURRENT_TIMESTAMP
        """,
        (
            company_id,
            connection_id,
            provider,
            basis,
            line_kind,
            external_invoice_id,
            external_payment_id,
            external_contact_id,
            contact_name,
            amount,
            currency[:3].upper(),
            posted_date,
            lead_id,
            idempotency_key,
        ),
    )


def sync_xero_for_company(cur, company_id: int) -> Dict[str, int]:
    row = _get_connection(cur, company_id, "xero")
    if not row:
        raise ValueError("No Xero connection for this company.")
    conn_id = row["id"]
    access, tenant_id = ensure_xero_access(cur, company_id)
    refreshed = _get_connection(cur, company_id, "xero")
    conn_id = refreshed["id"]

    invoices = xero_api.fetch_all_invoices(access, tenant_id)
    payments = xero_api.fetch_all_payments(access, tenant_id)
    inserted = 0

    for inv in invoices:
        if inv.get("Type") != "ACCREC":
            continue
        status = (inv.get("Status") or "").upper()
        if status in ("VOIDED", "DELETED", "DRAFT"):
            continue
        if status not in ("AUTHORISED", "PAID", "SUBMITTED"):
            continue
        inv_id = inv.get("InvoiceID")
        if not inv_id:
            continue
        contact = inv.get("Contact") or {}
        cid = contact.get("ContactID")
        cname = contact.get("Name")
        total = inv.get("Total")
        if total is None:
            continue
        amt = Decimal(str(total))
        cur_date = parse_xero_date(inv.get("Date"))
        if not cur_date:
            continue
        currency = (inv.get("CurrencyCode") or "GBP")[:3]
        idem = f"xero:{tenant_id}:accrual:invoice:{inv_id}"
        lid = _resolve_lead(cur, company_id, "xero", cid)
        _upsert_line(
            cur,
            company_id,
            conn_id,
            "xero",
            "accrual",
            "invoice_total",
            external_invoice_id=str(inv_id),
            external_payment_id=None,
            external_contact_id=str(cid) if cid else None,
            contact_name=cname,
            amount=amt,
            currency=currency,
            posted_date=cur_date,
            idempotency_key=idem,
            lead_id=lid,
        )
        inserted += 1

    for pay in payments:
        status = (pay.get("Status") or "").upper()
        if status in ("DELETED", "VOIDED"):
            continue
        inv = pay.get("Invoice") or {}
        inv_type = inv.get("Type")
        if inv_type and inv_type != "ACCREC":
            continue
        if not inv.get("InvoiceID"):
            continue
        pid = pay.get("PaymentID")
        if not pid:
            continue
        amt = pay.get("Amount")
        if amt is None:
            continue
        d = parse_xero_date(pay.get("Date"))
        if not d:
            continue
        currency = (pay.get("CurrencyCode") or "GBP")[:3]
        contact = inv.get("Contact") or pay.get("Contact") or {}
        cid = contact.get("ContactID")
        cname = contact.get("Name")
        idem = f"xero:{tenant_id}:cash:payment:{pid}"
        lid = _resolve_lead(cur, company_id, "xero", cid)
        _upsert_line(
            cur,
            company_id,
            conn_id,
            "xero",
            "cash",
            "invoice_payment",
            external_invoice_id=str(inv.get("InvoiceID")),
            external_payment_id=str(pid),
            external_contact_id=str(cid) if cid else None,
            contact_name=cname,
            amount=Decimal(str(amt)),
            currency=currency,
            posted_date=d,
            idempotency_key=idem,
            lead_id=lid,
        )
        inserted += 1

    return {"rows_upserted": inserted}


def _parse_qbo_date(val: Optional[str]) -> Optional[date]:
    if not val:
        return None
    try:
        return date.fromisoformat(val[:10])
    except ValueError:
        return None


def sync_quickbooks_for_company(cur, company_id: int) -> Dict[str, int]:
    row = _get_connection(cur, company_id, "quickbooks")
    if not row:
        raise ValueError("No QuickBooks connection for this company.")
    conn_id = row["id"]
    access, realm_id = ensure_qbo_access(cur, company_id)
    refreshed = _get_connection(cur, company_id, "quickbooks")
    conn_id = refreshed["id"]

    invoices = qbo_api.fetch_invoices(access, realm_id)
    payments = qbo_api.fetch_payments(access, realm_id)
    n = 0

    for inv in invoices:
        inv_id = inv.get("Id")
        if not inv_id:
            continue
        total = inv.get("TotalAmt")
        if total is None:
            continue
        txn_date = _parse_qbo_date(inv.get("TxnDate"))
        if not txn_date:
            continue
        cref = inv.get("CustomerRef") or {}
        cid = cref.get("value")
        cname = cref.get("name")
        currency = (inv.get("CurrencyRef", {}) or {}).get("value") or "GBP"
        idem = f"qbo:{realm_id}:accrual:invoice:{inv_id}"
        lid = _resolve_lead(cur, company_id, "quickbooks", str(cid) if cid else None)
        _upsert_line(
            cur,
            company_id,
            conn_id,
            "quickbooks",
            "accrual",
            "qbo_invoice",
            external_invoice_id=str(inv_id),
            external_payment_id=None,
            external_contact_id=str(cid) if cid else None,
            contact_name=cname,
            amount=Decimal(str(total)),
            currency=str(currency)[:3],
            posted_date=txn_date,
            idempotency_key=idem,
            lead_id=lid,
        )
        n += 1

    for pay in payments:
        pid = pay.get("Id")
        if not pid:
            continue
        amt = pay.get("TotalAmt")
        if amt is None:
            continue
        txn_date = _parse_qbo_date(pay.get("TxnDate"))
        if not txn_date:
            continue
        cref = pay.get("CustomerRef") or {}
        cid = cref.get("value")
        cname = cref.get("name")
        currency = (pay.get("CurrencyRef", {}) or {}).get("value") or "GBP"
        linked_inv = None
        for line in pay.get("Line") or []:
            for lt in line.get("LinkedTxn") or []:
                if lt.get("TxnType") == "Invoice":
                    linked_inv = str(lt.get("TxnId"))
                    break
            if linked_inv:
                break
        idem = f"qbo:{realm_id}:cash:payment:{pid}"
        lid = _resolve_lead(cur, company_id, "quickbooks", str(cid) if cid else None)
        _upsert_line(
            cur,
            company_id,
            conn_id,
            "quickbooks",
            "cash",
            "qbo_payment",
            external_invoice_id=linked_inv,
            external_payment_id=str(pid),
            external_contact_id=str(cid) if cid else None,
            contact_name=cname,
            amount=Decimal(str(amt)),
            currency=str(currency)[:3],
            posted_date=txn_date,
            idempotency_key=idem,
            lead_id=lid,
        )
        n += 1

    return {"rows_upserted": n}
