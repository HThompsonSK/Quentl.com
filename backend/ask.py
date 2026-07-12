"""
Natural-language Q&A over Quentl financial data via OpenAI tool calling.

Requires OPENAI_API_KEY in backend/.env (loaded by db.py / load_dotenv).
"""

import json
import os
from datetime import date
from decimal import Decimal
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from openai import OpenAI
from pydantic import BaseModel, Field

from db import get_db

router = APIRouter(prefix="/api", tags=["ask"])

SYSTEM_PROMPT = """You are Quentl, a financial assistant for founders.

Answer in plain English using 1–3 short sentences. Use only numbers returned by tools — never invent figures.
If a tool returns empty or null data, say what is missing and suggest connecting Xero or QuickBooks in Settings, or entering data in the relevant Quentl page.
Round money sensibly (e.g. £12.4k). Months of runway can be rounded to one decimal.
Do not mention tools, APIs, or JSON — speak directly to the founder."""

TOOLS: List[Dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "get_runway",
            "description": "Cash on hand, monthly fixed burn, weighted sales pipeline, one-off CAPEX, and runway in months.",
            "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_metrics",
            "description": "Latest SaaS metrics: MRR, churn, CAC, LTV, new customers, and related monthly_metrics fields.",
            "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_financial_overview",
            "description": "Recent forecast P&L rows: revenue, costs, net profit, gross margin by month from the financial model.",
            "parameters": {
                "type": "object",
                "properties": {
                    "months": {
                        "type": "integer",
                        "description": "How many recent forecast months to return (default 3, max 12).",
                    }
                },
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_actuals_summary",
            "description": "Accounting actuals from Xero/QuickBooks grouped by month — accrual (invoices) and cash (payments).",
            "parameters": {
                "type": "object",
                "properties": {
                    "months": {
                        "type": "integer",
                        "description": "How many recent months to return (default 6, max 24).",
                    }
                },
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_top_products",
            "description": "Top products by forecast revenue.",
            "parameters": {
                "type": "object",
                "properties": {
                    "limit": {
                        "type": "integer",
                        "description": "Number of products to return (default 5, max 10).",
                    }
                },
                "additionalProperties": False,
            },
        },
    },
]

TOOL_LABELS = {
    "get_runway": "runway & cash",
    "get_metrics": "key metrics",
    "get_financial_overview": "financial forecast",
    "get_actuals_summary": "accounting actuals",
    "get_top_products": "top products",
}


class AskBody(BaseModel):
    company_id: int = Field(..., ge=1)
    question: str = Field(..., min_length=1, max_length=2000)


class AskOut(BaseModel):
    answer: str
    sources: List[str] = Field(default_factory=list)


def _json_val(v: Any) -> Any:
    if v is None:
        return None
    if isinstance(v, Decimal):
        return float(v)
    if isinstance(v, date):
        return v.isoformat()
    if isinstance(v, dict):
        return {k: _json_val(val) for k, val in v.items()}
    if isinstance(v, list):
        return [_json_val(item) for item in v]
    return v


def _tool_get_runway(company_id: int, conn) -> Dict[str, Any]:
    from main import _runway_payload

    with conn.cursor() as cur:
        payload = _runway_payload(company_id, cur)
    return _json_val(payload)


def _tool_get_metrics(company_id: int, conn) -> Dict[str, Any]:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT * FROM monthly_metrics
            WHERE company_id = %s
            ORDER BY month_date DESC
            LIMIT 1
            """,
            (company_id,),
        )
        row = cur.fetchone()
    if not row:
        return {"company_id": company_id, "metrics": None, "note": "No metrics entered yet."}
    return {"company_id": company_id, "metrics": _json_val(dict(row))}


def _tool_get_financial_overview(company_id: int, conn, months: int = 3) -> Dict[str, Any]:
    months = max(1, min(months, 12))
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT forecast_month, revenue, fixed_costs, net_profit, gross_margin_percent
            FROM view_financial_health
            WHERE company_id = %s
            ORDER BY forecast_month DESC
            LIMIT %s
            """,
            (company_id, months),
        )
        rows = cur.fetchall()
    return {
        "company_id": company_id,
        "months": [_json_val(dict(r)) for r in rows],
        "note": "Forecast P&L from the financial model, not accounting actuals.",
    }


def _tool_get_actuals_summary(company_id: int, conn, months: int = 6) -> Dict[str, Any]:
    months = max(1, min(months, 24))
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
            LIMIT %s
            """,
            (company_id, months * 2),
        )
        rows = cur.fetchall()
    legend = {
        "accrual": "Invoice totals by invoice date.",
        "cash": "Customer payments by payment date.",
    }
    return {
        "company_id": company_id,
        "legend": legend,
        "by_month": [_json_val(dict(r)) for r in rows],
        "note": "Empty if Xero or QuickBooks is not connected or synced.",
    }


def _tool_get_top_products(company_id: int, conn, limit: int = 5) -> Dict[str, Any]:
    limit = max(1, min(limit, 10))
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT p.name,
                   SUM(f.units_forecasted * COALESCE(f.price_override, p.default_sales_price)) AS total_revenue
            FROM forecast_entries f
            JOIN products p ON f.product_id = p.id
            WHERE f.company_id = %s
            GROUP BY p.name
            ORDER BY total_revenue DESC
            LIMIT %s
            """,
            (company_id, limit),
        )
        rows = cur.fetchall()
    return {"company_id": company_id, "products": [_json_val(dict(r)) for r in rows]}


def _execute_tool(name: str, args: Dict[str, Any], company_id: int, conn) -> Dict[str, Any]:
    if name == "get_runway":
        return _tool_get_runway(company_id, conn)
    if name == "get_metrics":
        return _tool_get_metrics(company_id, conn)
    if name == "get_financial_overview":
        return _tool_get_financial_overview(company_id, conn, int(args.get("months") or 3))
    if name == "get_actuals_summary":
        return _tool_get_actuals_summary(company_id, conn, int(args.get("months") or 6))
    if name == "get_top_products":
        return _tool_get_top_products(company_id, conn, int(args.get("limit") or 5))
    return {"error": f"Unknown tool: {name}"}


def register_ask_routes(app) -> None:
    app.include_router(router)


@router.post("/ask", response_model=AskOut)
def ask_quentl(body: AskBody, conn=Depends(get_db)):
    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    if not api_key:
        raise HTTPException(
            status_code=503,
            detail="OPENAI_API_KEY is not set. Add it to backend/.env and restart the server.",
        )

    model = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
    client = OpenAI(api_key=api_key)
    messages: List[Dict[str, Any]] = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": body.question.strip()},
    ]
    sources: List[str] = []
    max_rounds = 6

    for _ in range(max_rounds):
        try:
            completion = client.chat.completions.create(
                model=model,
                messages=messages,
                tools=TOOLS,
                tool_choice="auto",
                temperature=0.2,
                max_tokens=400,
            )
        except Exception as e:
            raise HTTPException(status_code=502, detail=f"OpenAI request failed: {e}") from e

        choice = completion.choices[0]
        assistant_msg = choice.message

        if assistant_msg.tool_calls:
            messages.append(
                {
                    "role": "assistant",
                    "content": assistant_msg.content or "",
                    "tool_calls": [
                        {
                            "id": tc.id,
                            "type": "function",
                            "function": {
                                "name": tc.function.name,
                                "arguments": tc.function.arguments or "{}",
                            },
                        }
                        for tc in assistant_msg.tool_calls
                    ],
                }
            )
            for tc in assistant_msg.tool_calls:
                fn = tc.function.name
                try:
                    args = json.loads(tc.function.arguments or "{}")
                except json.JSONDecodeError:
                    args = {}
                result = _execute_tool(fn, args, body.company_id, conn)
                label = TOOL_LABELS.get(fn, fn)
                if label not in sources:
                    sources.append(label)
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": json.dumps(result),
                    }
                )
            continue

        answer = (assistant_msg.content or "").strip()
        if not answer:
            raise HTTPException(status_code=502, detail="Model returned an empty answer.")
        return AskOut(answer=answer, sources=sources)

    raise HTTPException(status_code=502, detail="Too many tool rounds; try a simpler question.")
