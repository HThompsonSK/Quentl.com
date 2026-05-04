"""
Sketch API for conversational onboarding (Layer 2) and calibration hooks (Layer 3).

Requires migration: database/migrations/002_onboarding_sketch.sql

Chat does not call an LLM here — assistant rows are stubs so routes and DB shapes stay testable.
"""

import json
from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from db import get_db
from integrations.sync_service import sync_quickbooks_for_company, sync_xero_for_company

router = APIRouter(prefix="/api/onboarding", tags=["onboarding-sketch"])


# --- Request / response models (OpenAPI sketch) ---


class OnboardingSessionCreate(BaseModel):
    company_id: int = Field(..., ge=1)
    user_id: Optional[int] = Field(None, ge=1)
    """If omitted, interview is still scoped to company_id only."""


class OnboardingSessionOut(BaseModel):
    id: int
    company_id: int
    user_id: Optional[int]
    status: str
    current_step_key: Optional[str]
    integration_snapshot: Dict[str, Any]
    created_at: Optional[datetime] = None


class OnboardingMessageCreate(BaseModel):
    company_id: int = Field(..., ge=1)
    role: str = Field(..., pattern="^(user|assistant|system)$")
    content: str = Field(..., min_length=1)
    structured_patch: Optional[Dict[str, Any]] = None


class OnboardingMessageOut(BaseModel):
    id: int
    session_id: int
    role: str
    content: str
    structured_patch: Optional[Dict[str, Any]] = None
    created_at: Optional[datetime] = None


class ProfileUpsertBody(BaseModel):
    company_id: int = Field(..., ge=1)
    data: Dict[str, Any] = Field(default_factory=dict)
    merge: bool = True
    """If True, merge `data` into existing JSON; if False, replace."""


class CalibrationEnqueueBody(BaseModel):
    company_id: int = Field(..., ge=1)
    request_xero_sync: bool = False
    request_qbo_sync: bool = False
    """When true, runs the same sync as POST /api/integrations/xero|quickbooks/sync inside this request."""


class CalibrationRunOut(BaseModel):
    id: int
    company_id: int
    session_id: Optional[int]
    profile_id: Optional[int]
    status: str
    steps: Dict[str, Any]
    sync_xero_requested_at: Optional[datetime] = None
    sync_qbo_requested_at: Optional[datetime] = None
    error: Optional[str] = None
    created_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None


class ApplyPlanOut(BaseModel):
    """What a full implementation would do after profile lock; no writes except documented."""

    company_id: int
    profile_keys: List[str]
    suggested_sync: Dict[str, bool]
    notes: List[str]


# --- Helpers ---


def _load_integration_snapshot(cur, company_id: int) -> Dict[str, Any]:
    cur.execute(
        """
        SELECT service_name FROM api_connections WHERE company_id = %s
        """,
        (company_id,),
    )
    rows = cur.fetchall() or []
    snap: Dict[str, Any] = {}
    for r in rows:
        name = (r.get("service_name") or "").lower()
        if name in ("xero", "quickbooks"):
            snap[name] = True
    return snap


def _get_session(cur, session_id: int, company_id: int) -> Optional[Dict[str, Any]]:
    cur.execute(
        """
        SELECT * FROM onboarding_sessions
        WHERE id = %s AND company_id = %s
        """,
        (session_id, company_id),
    )
    return cur.fetchone()


STUB_ASSISTANT = (
    "[stub] Thanks — noted. Next: describe your main revenue streams "
    "(e.g. subscription vs one-off). A production agent would branch from your Layer-2 script here."
)


# --- Routes ---


@router.post("/sessions", response_model=OnboardingSessionOut)
def create_session(body: OnboardingSessionCreate, conn=Depends(get_db)):
    with conn.cursor() as cur:
        snap = _load_integration_snapshot(cur, body.company_id)
        cur.execute(
            """
            INSERT INTO onboarding_sessions (company_id, user_id, status, integration_snapshot)
            VALUES (%s, %s, 'in_progress', %s::jsonb)
            RETURNING *
            """,
            (body.company_id, body.user_id, json.dumps(snap)),
        )
        row = cur.fetchone()
        conn.commit()
    return row


@router.get("/sessions/{session_id}", response_model=OnboardingSessionOut)
def get_session(session_id: int, company_id: int, conn=Depends(get_db)):
    with conn.cursor() as cur:
        row = _get_session(cur, session_id, company_id)
    if not row:
        raise HTTPException(404, "Session not found for this company.")
    return row


@router.get("/sessions/{session_id}/messages", response_model=List[OnboardingMessageOut])
def list_messages(session_id: int, company_id: int, conn=Depends(get_db)):
    with conn.cursor() as cur:
        if not _get_session(cur, session_id, company_id):
            raise HTTPException(404, "Session not found for this company.")
        cur.execute(
            """
            SELECT * FROM onboarding_messages
            WHERE session_id = %s
            ORDER BY id ASC
            """,
            (session_id,),
        )
        rows = cur.fetchall() or []
    return rows


@router.post("/sessions/{session_id}/messages", response_model=List[OnboardingMessageOut])
def append_message(session_id: int, body: OnboardingMessageCreate, conn=Depends(get_db)):
    if body.role != "user":
        raise HTTPException(400, "Clients should POST role=user; assistant rows are created server-side.")
    with conn.cursor() as cur:
        if not _get_session(cur, session_id, body.company_id):
            raise HTTPException(404, "Session not found for this company.")
        cur.execute(
            """
            INSERT INTO onboarding_messages (session_id, role, content, structured_patch)
            VALUES (%s, %s, %s, %s::jsonb)
            RETURNING *
            """,
            (
                session_id,
                body.role,
                body.content,
                json.dumps(body.structured_patch) if body.structured_patch is not None else None,
            ),
        )
        user_row = cur.fetchone()
        cur.execute(
            """
            INSERT INTO onboarding_messages (session_id, role, content, structured_patch)
            VALUES (%s, 'assistant', %s, NULL)
            RETURNING *
            """,
            (session_id, STUB_ASSISTANT),
        )
        asst_row = cur.fetchone()
        cur.execute(
            "UPDATE onboarding_sessions SET updated_at = CURRENT_TIMESTAMP WHERE id = %s",
            (session_id,),
        )
        conn.commit()
    return [user_row, asst_row]


@router.put("/profiles", response_model=Dict[str, Any])
def upsert_profile(body: ProfileUpsertBody, conn=Depends(get_db)):
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id, data FROM company_onboarding_profiles WHERE company_id = %s",
            (body.company_id,),
        )
        existing = cur.fetchone()
        if existing and body.merge:
            merged = dict(existing.get("data") or {})
            merged.update(body.data)
            payload = json.dumps(merged)
            cur.execute(
                """
                UPDATE company_onboarding_profiles
                SET data = %s::jsonb, updated_at = CURRENT_TIMESTAMP, profile_version = profile_version + 1
                WHERE company_id = %s
                RETURNING *
                """,
                (payload, body.company_id),
            )
        elif existing and not body.merge:
            payload = json.dumps(body.data)
            cur.execute(
                """
                UPDATE company_onboarding_profiles
                SET data = %s::jsonb, updated_at = CURRENT_TIMESTAMP, profile_version = profile_version + 1
                WHERE company_id = %s
                RETURNING *
                """,
                (payload, body.company_id),
            )
        else:
            payload = json.dumps(body.data)
            cur.execute(
                """
                INSERT INTO company_onboarding_profiles (company_id, data)
                VALUES (%s, %s::jsonb)
                RETURNING *
                """,
                (body.company_id, payload),
            )
        row = cur.fetchone()
        conn.commit()
    return row


@router.get("/profiles", response_model=Optional[Dict[str, Any]])
def get_profile(company_id: int, conn=Depends(get_db)):
    with conn.cursor() as cur:
        cur.execute(
            "SELECT * FROM company_onboarding_profiles WHERE company_id = %s",
            (company_id,),
        )
        row = cur.fetchone()
    return row


@router.post("/sessions/{session_id}/calibration-runs", response_model=CalibrationRunOut)
def enqueue_calibration(session_id: int, body: CalibrationEnqueueBody, conn=Depends(get_db)):
    """
    Creates a calibration run, optionally calling the same sync logic as
    POST /api/integrations/xero/sync and quickbooks/sync (each committed separately).
    """
    steps: Dict[str, Any] = {}
    failures: List[str] = []
    run_id: Optional[int] = None

    try:
        with conn.cursor() as cur:
            sess = _get_session(cur, session_id, body.company_id)
            if not sess:
                raise HTTPException(404, "Session not found for this company.")
            cur.execute(
                "SELECT id FROM company_onboarding_profiles WHERE company_id = %s",
                (body.company_id,),
            )
            prof = cur.fetchone()
            profile_id = prof["id"] if prof else None
            steps = {
                "extract_profile": "ok" if profile_id else "skipped_no_profile",
            }
            cur.execute(
                """
                INSERT INTO onboarding_calibration_runs (
                    company_id, session_id, profile_id, status, steps,
                    sync_xero_requested_at, sync_qbo_requested_at
                )
                VALUES (
                    %s, %s, %s, 'running', %s::jsonb,
                    CASE WHEN %s THEN CURRENT_TIMESTAMP ELSE NULL END,
                    CASE WHEN %s THEN CURRENT_TIMESTAMP ELSE NULL END
                )
                RETURNING id
                """,
                (
                    body.company_id,
                    session_id,
                    profile_id,
                    json.dumps(steps),
                    body.request_xero_sync,
                    body.request_qbo_sync,
                ),
            )
            run_row = cur.fetchone()
            run_id = int(run_row["id"])
        conn.commit()

        if body.request_xero_sync:
            try:
                with conn.cursor() as cur:
                    result = sync_xero_for_company(cur, body.company_id)
                conn.commit()
                steps["sync_xero"] = "ok"
                steps["sync_xero_result"] = dict(result)
            except Exception as e:
                conn.rollback()
                msg = str(e)
                steps["sync_xero"] = "failed"
                steps["sync_xero_error"] = msg
                failures.append(f"xero: {msg}")
        else:
            steps["sync_xero"] = "skipped"

        if body.request_qbo_sync:
            try:
                with conn.cursor() as cur:
                    result = sync_quickbooks_for_company(cur, body.company_id)
                conn.commit()
                steps["sync_qbo"] = "ok"
                steps["sync_qbo_result"] = dict(result)
            except Exception as e:
                conn.rollback()
                msg = str(e)
                steps["sync_qbo"] = "failed"
                steps["sync_qbo_error"] = msg
                failures.append(f"quickbooks: {msg}")
        else:
            steps["sync_qbo"] = "skipped"

        final_status = "failed" if failures else "ok"
        err_summary = "; ".join(failures) if failures else None

        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE onboarding_calibration_runs
                SET status = %s,
                    steps = %s::jsonb,
                    error = %s,
                    completed_at = CURRENT_TIMESTAMP
                WHERE id = %s
                RETURNING *
                """,
                (final_status, json.dumps(steps), err_summary, run_id),
            )
            row = cur.fetchone()
        conn.commit()
        return row
    except HTTPException:
        conn.rollback()
        raise
    except Exception as e:
        conn.rollback()
        if run_id is not None:
            try:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        UPDATE onboarding_calibration_runs
                        SET status = 'failed',
                            steps = %s::jsonb,
                            error = %s,
                            completed_at = CURRENT_TIMESTAMP
                        WHERE id = %s
                        """,
                        (json.dumps(steps), str(e), run_id),
                    )
                conn.commit()
            except Exception:
                conn.rollback()
        raise HTTPException(500, str(e)) from e


@router.get("/sessions/{session_id}/apply-plan", response_model=ApplyPlanOut)
def apply_plan(session_id: int, company_id: int, conn=Depends(get_db)):
    """Read-only sketch of Layer-3 side effects; implement worker to mutate products/metrics etc."""

    with conn.cursor() as cur:
        if not _get_session(cur, session_id, company_id):
            raise HTTPException(404, "Session not found for this company.")
        cur.execute(
            "SELECT data FROM company_onboarding_profiles WHERE company_id = %s",
            (company_id,),
        )
        prof = cur.fetchone()
        data = (prof or {}).get("data") or {}
        keys = sorted(data.keys()) if isinstance(data, dict) else []
        snap = _load_integration_snapshot(cur, company_id)
    return ApplyPlanOut(
        company_id=company_id,
        profile_keys=keys,
        suggested_sync={"xero": snap.get("xero") is True, "quickbooks": snap.get("quickbooks") is True},
        notes=[
            "Implement: merge profile into products / fixed_expenses / monthly_metrics.",
            "POST .../calibration-runs with request_xero_sync or request_qbo_sync runs the same ingest as the integrations sync endpoints (each committed separately).",
            "Mark onboarding_sessions.completed_at when calibration run status = ok.",
        ],
    )


def register_onboarding_sketch_routes(app):
    app.include_router(router)
