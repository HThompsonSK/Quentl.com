import os
from fastapi import FastAPI, HTTPException, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from pydantic import BaseModel
from typing import List, Optional, Any
from datetime import date
from decimal import Decimal

from db import get_db
from integrations.router import register_integration_routes
from onboarding_sketch import register_onboarding_sketch_routes
from ask import register_ask_routes

app = FastAPI()

# Served by StaticFiles mount below — used for HTML alias redirects.
frontend_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "../frontend"))
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class NoCacheHtmlMiddleware(BaseHTTPMiddleware):
    """Avoid stale nav/shell markup when editing static HTML (browser disk cache)."""

    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        ct = response.headers.get("content-type", "")
        if any(
            part in ct
            for part in ("text/html", "text/css", "application/javascript", "text/javascript")
        ):
            response.headers["Cache-Control"] = "no-store, max-age=0, must-revalidate"
            response.headers["Pragma"] = "no-cache"
        return response


app.add_middleware(NoCacheHtmlMiddleware)

register_integration_routes(app)
register_onboarding_sketch_routes(app)
register_ask_routes(app)

# --- PYDANTIC MODELS ---

from typing import List, Optional, Any

class MoveCashflow(BaseModel):
    old_month: str  # e.g., "2026-05"
    new_month: str  # e.g., "2026-06"

class InstallmentCreate(BaseModel):
    payment_amount: float
    expected_payment_date: date

class ProjectBudgetWithTermsCreate(BaseModel):
    item_name: str
    supplier: Optional[str] = None
    grouping_category: Optional[str] = None
    total_amount: float
    start_date: date
    installments: List[InstallmentCreate]

class MetricCreate(BaseModel):
    company_id: int
    month_date: date
    mau: int = 0
    cac: float = 0.0
    churn_rate: float = 0.0
    ltv: float = 0.0
    revenue_per_employee: float = 0.0
    mrr: Optional[float] = None
    expansion_mrr: Optional[float] = None
    contraction_mrr: Optional[float] = None
    churned_mrr: Optional[float] = None
    starting_mrr: Optional[float] = None
    new_customers: Optional[int] = None
    sm_spend: Optional[float] = None
    customers_start_of_month: Optional[int] = None
    customers_lost: Optional[int] = None


def _json_num(v: Any) -> Any:
    if v is None:
        return None
    if isinstance(v, Decimal):
        return float(v)
    if isinstance(v, date):
        return v.isoformat()
    return v

class ProjectCreate(BaseModel):
    company_id: int
    project_name: str
    status: Optional[str] = 'planned'

class ProjectBudgetCreate(BaseModel):
    item_name: str
    budgeted_amount: float
    expected_date: date

class ProductCreate(BaseModel):
    company_id: int
    name: str
    sku: str
    price: float
    cogs: float

class ExpenseCreate(BaseModel):
    company_id: int
    name: str
    amount: float

class ForecastCreate(BaseModel):
    company_id: int
    product_id: int
    month: str
    units: int

class LeadCreate(BaseModel):
    company_id: int
    client_name: str
    contract_value: float
    probability: int  # Matching your SQL database type
    expected_close_date: Optional[date] = None

class CashBalanceCreate(BaseModel):
    company_id: int
    balance_date: date
    total_cash: float

class OneOffExpenseCreate(BaseModel):
    company_id: int
    name: str
    amount: float
    expense_date: date

# --- 1. CORE DASHBOARD ENDPOINTS ---

@app.get("/api/dashboard/{company_id}")
def get_dashboard(company_id: int, conn=Depends(get_db)):
    try:
        with conn.cursor() as cur:
            cur.execute(
                'SELECT * FROM view_financial_health WHERE company_id = %s ORDER BY forecast_month ASC',
                (company_id,)
            )
            return cur.fetchall()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/dashboard/{company_id}/details/{month}")
def get_dashboard_details(company_id: int, month: str, conn=Depends(get_db)):
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT f.id, p.name, f.units_forecasted, 
                       (f.units_forecasted * COALESCE(f.price_override, p.default_sales_price)) as revenue
                FROM forecast_entries f
                JOIN products p ON f.product_id = p.id
                WHERE f.company_id = %s AND f.forecast_month = %s
            """, (company_id, month))
            return cur.fetchall()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# --- 2. ANALYTICS ENDPOINTS ---

@app.get("/api/analytics/top-products/{company_id}")
def get_top_products(company_id: int, conn=Depends(get_db)):
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT p.name, SUM(f.units_forecasted * COALESCE(f.price_override, p.default_sales_price)) as total_revenue
                FROM forecast_entries f
                JOIN products p ON f.product_id = p.id
                WHERE f.company_id = %s
                GROUP BY p.name
                ORDER BY total_revenue DESC
                LIMIT 5
            """, (company_id,))
            return cur.fetchall()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# --- 3. INPUT & DELETE ENDPOINTS ---

@app.put("/api/projects/budget/{budget_item_id}/move")
def move_cashflow(budget_item_id: int, item: MoveCashflow, conn=Depends(get_db)):
    try:
        with conn.cursor() as cur:
            # Shift any installments in the old month to the 1st day of the new month
            cur.execute("""
                UPDATE project_budget_installments 
                SET expected_payment_date = TO_DATE(%s || '-01', 'YYYY-MM-DD')
                WHERE budget_item_id = %s 
                AND TO_CHAR(expected_payment_date, 'YYYY-MM') = %s
            """, (item.new_month, budget_item_id, item.old_month))
            conn.commit()
            return {"success": True}
    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/products")
def get_products(company_id: int, conn=Depends(get_db)):
    try:
        with conn.cursor() as cur:
            cur.execute('SELECT * FROM products WHERE company_id = %s', (company_id,))
            return cur.fetchall()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/products")
def add_product(item: ProductCreate, conn=Depends(get_db)):
    try:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO products (company_id, name, sku, default_sales_price, default_cogs)
                VALUES (%s, %s, %s, %s, %s) RETURNING *
            """, (item.company_id, item.name, item.sku, item.price, item.cogs))
            result = cur.fetchone()
            conn.commit()
            return result
    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/expenses")
def add_expense(item: ExpenseCreate, conn=Depends(get_db)):
    try:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO fixed_expenses (company_id, name, amount, frequency)
                VALUES (%s, %s, %s, 'monthly') RETURNING *
            """, (item.company_id, item.name, item.amount))
            result = cur.fetchone()
            conn.commit()
            return result
    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/forecast")
def add_forecast(item: ForecastCreate, conn=Depends(get_db)):
    try:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO forecast_entries (company_id, product_id, forecast_month, units_forecasted)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (company_id, product_id, forecast_month) 
                DO UPDATE SET units_forecasted = %s
                RETURNING *
            """, (item.company_id, item.product_id, item.month, item.units, item.units))
            result = cur.fetchone()
            conn.commit()
            return result
    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail=str(e))

@app.delete("/api/forecast/{item_id}")
def delete_forecast(item_id: int, conn=Depends(get_db)):
    try:
        with conn.cursor() as cur:
            cur.execute('DELETE FROM forecast_entries WHERE id = %s', (item_id,))
            conn.commit()
            return {"success": True}
    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail=str(e))

# --- CRM / PIPELINE ENDPOINTS ---

@app.get("/api/leads/{company_id}")
def get_leads(company_id: int, conn=Depends(get_db)):
    try:
        with conn.cursor() as cur:
            cur.execute('SELECT * FROM leads WHERE company_id = %s ORDER BY probability DESC', (company_id,))
            return cur.fetchall()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/leads")
def add_lead(item: LeadCreate, conn=Depends(get_db)):
    try:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO leads (company_id, client_name, contract_value, probability, expected_close_date)
                VALUES (%s, %s, %s, %s, %s) RETURNING *
            """, (item.company_id, item.client_name, item.contract_value, item.probability, item.expected_close_date))
            result = cur.fetchone()
            conn.commit()
            return result
    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail=str(e))

@app.delete("/api/leads/{item_id}")
def delete_lead(item_id: int, conn=Depends(get_db)):
    try:
        with conn.cursor() as cur:
            cur.execute('DELETE FROM leads WHERE id = %s', (item_id,))
            conn.commit()
            return {"success": True}
    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail=str(e))

@app.delete("/api/projects/budget/{budget_item_id}")
def delete_budget_item(budget_item_id: int, conn=Depends(get_db)):
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM project_budgets WHERE id = %s", (budget_item_id,))
            conn.commit()
            return {"success": True}
    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail=str(e))

# ---- FOUNDER OS: PROJECT CASHFLOW VIEW (BUDGET ITEMS + PAYMENT SCHEDULE) ---

@app.get("/api/projects/{project_id}/cashflow")
def get_project_cashflow(project_id: int, conn=Depends(get_db)):
    try:
        with conn.cursor() as cur:
            # 1. Get all the budget items (the rows)
            cur.execute("""
                SELECT * FROM project_budgets 
                WHERE project_id = %s 
                ORDER BY start_date ASC
            """, (project_id,))
            budgets = cur.fetchall()

            # 2. For each row, get the exact payment installments
            for budget in budgets:
                cur.execute("""
                    SELECT * FROM project_budget_installments 
                    WHERE budget_item_id = %s 
                    ORDER BY expected_payment_date ASC
                """, (budget['id'],))
                budget['installments'] = cur.fetchall()

            return budgets
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# --- CASH BALANCES ---
@app.get("/api/cash_balances/{company_id}")
def get_cash_balances(company_id: int, conn=Depends(get_db)):
    try:
        with conn.cursor() as cur:
            cur.execute('SELECT * FROM cash_balances WHERE company_id = %s ORDER BY balance_date DESC', (company_id,))
            return cur.fetchall()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/cash_balances")
def add_cash_balance(item: CashBalanceCreate, conn=Depends(get_db)):
    try:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO cash_balances (company_id, balance_date, total_cash)
                VALUES (%s, %s, %s) RETURNING *
            """, (item.company_id, item.balance_date, item.total_cash))
            result = cur.fetchone()
            conn.commit()
            return result
    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail=str(e))

# --- ONE-OFF EXPENSES ---
@app.get("/api/one_off_expenses/{company_id}")
def get_one_off_expenses(company_id: int, conn=Depends(get_db)):
    try:
        with conn.cursor() as cur:
            cur.execute('SELECT * FROM one_off_expenses WHERE company_id = %s ORDER BY expense_date ASC', (company_id,))
            return cur.fetchall()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/one_off_expenses")
def add_one_off_expense(item: OneOffExpenseCreate, conn=Depends(get_db)):
    try:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO one_off_expenses (company_id, name, amount, expense_date)
                VALUES (%s, %s, %s, %s) RETURNING *
            """, (item.company_id, item.name, item.amount, item.expense_date))
            result = cur.fetchone()
            conn.commit()
            return result
    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail=str(e))



# --- 4. FOUNDER OS: PROJECT CAPEX & BUDGETS ---

@app.post("/api/projects")
def create_project(item: ProjectCreate, conn=Depends(get_db)):
    try:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO projects (company_id, project_name, status)
                VALUES (%s, %s, %s) RETURNING *
            """, (item.company_id, item.project_name, item.status))
            result = cur.fetchone()
            conn.commit()
            return result
    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/projects/{project_id}/budget")
def add_project_budget_with_terms(project_id: int, item: ProjectBudgetWithTermsCreate, conn=Depends(get_db)):
    try:
        with conn.cursor() as cur:
            # 1. Insert the Parent Row
            cur.execute("""
                INSERT INTO project_budgets (project_id, item_name, supplier, grouping_category, total_amount, start_date)
                VALUES (%s, %s, %s, %s, %s, %s) RETURNING id
            """, (project_id, item.item_name, item.supplier, item.grouping_category, item.total_amount, item.start_date))
            
            budget_item_id = cur.fetchone()['id']

            # 2. Insert the Child Rows (The Payment Schedule)
            for inst in item.installments:
                cur.execute("""
                    INSERT INTO project_budget_installments (budget_item_id, payment_amount, expected_payment_date)
                    VALUES (%s, %s, %s)
                """, (budget_item_id, inst.payment_amount, inst.expected_payment_date))

            conn.commit()
            return {"success": True, "budget_item_id": budget_item_id}
    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/projects/{project_id}/financials")
def get_project_financials(project_id: int, conn=Depends(get_db)):
    try:
        with conn.cursor() as cur:
            # FIX: We changed 'budgeted_amount' to 'total_amount' to match the new schema!
            cur.execute("""
                SELECT SUM(total_amount) as total_budget 
                FROM project_budgets 
                WHERE project_id = %s
            """, (project_id,))
            budget_row = cur.fetchone()
            total_budget = float(budget_row['total_budget']) if budget_row and budget_row['total_budget'] else 0.0

            # 2. Get the Total Actual Spend (from one_off_expenses linked to this project)
            cur.execute("""
                SELECT SUM(amount) as total_actuals 
                FROM one_off_expenses 
                WHERE project_id = %s
            """, (project_id,))
            actuals_row = cur.fetchone()
            total_actuals = float(actuals_row['total_actuals']) if actuals_row and actuals_row['total_actuals'] else 0.0

            # 3. Calculate Variance
            variance = total_budget - total_actuals
            
            # Determine Status
            status = "On Budget"
            if variance < 0:
                status = "Over Budget"
            elif variance > 0 and total_actuals > 0:
                status = "Under Budget"

            return {
                "project_id": project_id,
                "total_budget": total_budget,
                "total_actuals": total_actuals,
                "variance": variance,
                "status": status
            }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


def _runway_payload(company_id: int, cur) -> dict:
    cur.execute(
        """
        SELECT total_cash
        FROM cash_balances
        WHERE company_id = %s
        ORDER BY balance_date DESC
        LIMIT 1
        """,
        (company_id,),
    )
    cash_row = cur.fetchone()
    starting_cash = float(cash_row["total_cash"]) if cash_row and cash_row.get("total_cash") is not None else 0.0

    cur.execute(
        """
        SELECT SUM(amount) as burn_rate
        FROM fixed_expenses
        WHERE company_id = %s
        """,
        (company_id,),
    )
    burn_row = cur.fetchone()
    burn_rate = float(burn_row["burn_rate"]) if burn_row and burn_row.get("burn_rate") else 0.0

    cur.execute(
        """
        SELECT SUM(contract_value * (probability / 100.0)) as expected_cash
        FROM leads
        WHERE company_id = %s
        """,
        (company_id,),
    )
    expected_row = cur.fetchone()
    expected_cash = float(expected_row["expected_cash"]) if expected_row and expected_row.get("expected_cash") else 0.0

    cur.execute(
        """
        SELECT SUM(amount) as total_capex
        FROM one_off_expenses
        WHERE company_id = %s
        """,
        (company_id,),
    )
    capex_row = cur.fetchone()
    capex = float(capex_row["total_capex"]) if capex_row and capex_row.get("total_capex") else 0.0

    gross_burn = burn_rate
    cur.execute(
        """
        SELECT revenue FROM view_financial_health
        WHERE company_id = %s
        ORDER BY forecast_month DESC
        LIMIT 1
        """,
        (company_id,),
    )
    rev_row = cur.fetchone()
    latest_month_revenue = float(rev_row["revenue"]) if rev_row and rev_row.get("revenue") is not None else 0.0
    net_burn = gross_burn - latest_month_revenue

    runway_pipeline_adjusted = None
    runway_net_burn_months = None
    if burn_rate == 0:
        runway_pipeline_adjusted = None
        status_message = "Infinite runway - no expenses logged"
        status_color = "Green"
    else:
        runway_pipeline_adjusted = (starting_cash + expected_cash - capex) / burn_rate
        status_message = f"{runway_pipeline_adjusted:.1f} months"
        if runway_pipeline_adjusted >= 6:
            status_color = "Green"
        elif runway_pipeline_adjusted >= 3:
            status_color = "Yellow"
        else:
            status_color = "Red"

    if net_burn is not None and net_burn > 0:
        runway_net_burn_months = starting_cash / net_burn
    elif net_burn is not None and net_burn <= 0:
        runway_net_burn_months = None

    return {
        "company_id": company_id,
        "starting_cash": starting_cash,
        "burn_rate": burn_rate,
        "gross_burn": gross_burn,
        "latest_month_revenue": latest_month_revenue,
        "net_burn": net_burn,
        "expected_cash": expected_cash,
        "capex": capex,
        "estimated_months_left": runway_pipeline_adjusted,
        "runway_net_burn_months": runway_net_burn_months,
        "status_message": status_message if burn_rate != 0 else "Infinite runway - no expenses logged",
        "status_color": status_color if burn_rate != 0 else "Green",
        "runway_definition_note": "Pipeline-adjusted uses (cash + weighted pipeline − total one-off CAPEX) / fixed monthly burn. Net-burn runway uses cash / (gross burn − latest forecast month revenue).",
    }


# --- 5. SOLVENCY ENGINE (PREDICTIVE LOGIC) ---

@app.get("/api/runway/{company_id}")
def get_runway(company_id: int, conn=Depends(get_db)):
    try:
        with conn.cursor() as cur:
            payload = _runway_payload(company_id, cur)
        return {
            "company_id": payload["company_id"],
            "starting_cash": payload["starting_cash"],
            "burn_rate": payload["burn_rate"],
            "expected_cash": payload["expected_cash"],
            "capex": payload["capex"],
            "estimated_months_left": payload["estimated_months_left"],
            "status_message": payload["status_message"],
            "status_color": payload["status_color"],
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# --- 6. FOUNDER OS: 5-MATRIX METRICS ---

@app.get("/api/metrics/{company_id}")
def get_latest_metrics(company_id: int, conn=Depends(get_db)):
    try:
        with conn.cursor() as cur:
            # Fetch the most recent month's data
            cur.execute("""
                SELECT * FROM monthly_metrics 
                WHERE company_id = %s 
                ORDER BY month_date DESC 
                LIMIT 1
            """, (company_id,))
            result = cur.fetchone()
            if not result:
                return {}
            return {k: _json_num(v) for k, v in result.items()}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/metrics")
def add_metrics(item: MetricCreate, conn=Depends(get_db)):
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO monthly_metrics (
                    company_id, month_date, mau, cac, churn_rate, ltv, revenue_per_employee,
                    mrr, expansion_mrr, contraction_mrr, churned_mrr, starting_mrr,
                    new_customers, sm_spend, customers_start_of_month, customers_lost
                )
                VALUES (
                    %s, %s, %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, %s,
                    %s, %s, %s, %s
                )
                ON CONFLICT (company_id, month_date)
                DO UPDATE SET
                    mau = EXCLUDED.mau,
                    cac = EXCLUDED.cac,
                    churn_rate = EXCLUDED.churn_rate,
                    ltv = EXCLUDED.ltv,
                    revenue_per_employee = EXCLUDED.revenue_per_employee,
                    mrr = EXCLUDED.mrr,
                    expansion_mrr = EXCLUDED.expansion_mrr,
                    contraction_mrr = EXCLUDED.contraction_mrr,
                    churned_mrr = EXCLUDED.churned_mrr,
                    starting_mrr = EXCLUDED.starting_mrr,
                    new_customers = EXCLUDED.new_customers,
                    sm_spend = EXCLUDED.sm_spend,
                    customers_start_of_month = EXCLUDED.customers_start_of_month,
                    customers_lost = EXCLUDED.customers_lost,
                    updated_at = CURRENT_TIMESTAMP
                RETURNING *
                """,
                (
                    item.company_id,
                    item.month_date,
                    item.mau,
                    item.cac,
                    item.churn_rate,
                    item.ltv,
                    item.revenue_per_employee,
                    item.mrr,
                    item.expansion_mrr,
                    item.contraction_mrr,
                    item.churned_mrr,
                    item.starting_mrr,
                    item.new_customers,
                    item.sm_spend,
                    item.customers_start_of_month,
                    item.customers_lost,
                ),
            )
            result = cur.fetchone()
            conn.commit()
            return {k: _json_num(v) for k, v in result.items()}
    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/financial-model/pl/{company_id}")
def financial_model_pl(company_id: int, conn=Depends(get_db)):
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT * FROM view_financial_health
                WHERE company_id = %s
                ORDER BY forecast_month ASC
                """,
                (company_id,),
            )
            fh = cur.fetchall()
            if not fh:
                return {
                    "columns": [],
                    "rows": [],
                    "by_product_month": [],
                }

            cols = [r["forecast_month"].isoformat() if hasattr(r["forecast_month"], "isoformat") else str(r["forecast_month"]) for r in fh]

            def cells(key):
                return [_json_num(r.get(key)) for r in fh]

            dol_cells = []
            for r in fh:
                gp = float(r["gross_profit"] or 0)
                np = float(r["net_profit"] or 0)
                if np == 0:
                    dol_cells.append(None)
                else:
                    dol_cells.append(round(gp / np, 4))

            rev_cells = cells("revenue")
            rule40_cells = []
            for i, r in enumerate(fh):
                yoy = None
                if i >= 12:
                    prev = float(fh[i - 12]["revenue"] or 0)
                    curv = float(r["revenue"] or 0)
                    if prev > 0:
                        yoy = round(((curv - prev) / prev) * 100, 2)
                ebitda_margin_proxy = None
                npf = float(r["net_profit"] or 0)
                revf = float(r["revenue"] or 0)
                if revf > 0:
                    ebitda_margin_proxy = round((npf / revf) * 100, 2)
                if yoy is not None and ebitda_margin_proxy is not None:
                    rule40_cells.append(round(yoy + ebitda_margin_proxy, 2))
                else:
                    rule40_cells.append(None)

            rows_out = [
                {"key": "revenue", "label": "Revenue", "cells": rev_cells},
                {"key": "variable_costs", "label": "Variable costs (COGS)", "cells": cells("variable_costs")},
                {"key": "contribution_margin", "label": "Contribution margin", "cells": cells("gross_profit")},
                {"key": "fixed_costs", "label": "Fixed costs (monthly)", "cells": cells("fixed_costs")},
                {"key": "net_profit", "label": "Net profit (operating-style)", "cells": cells("net_profit")},
                {"key": "gross_margin_percent", "label": "Gross margin %", "cells": cells("gross_margin_percent")},
                {"key": "dol", "label": "DOL (CM / net profit, approximate)", "cells": dol_cells},
                {"key": "rule_of_40", "label": "Rule of 40 (YoY rev % + net margin % proxy)", "cells": rule40_cells},
            ]

            cur.execute(
                """
                SELECT
                    product_name,
                    forecast_month,
                    units_forecasted,
                    final_price,
                    final_cogs,
                    (final_price - final_cogs) AS cm_per_unit,
                    CASE
                        WHEN final_price > 0 THEN ROUND(((final_price - final_cogs) / final_price) * 100.0, 2)
                        ELSE NULL
                    END AS cm_ratio_percent
                FROM view_forecast_details
                WHERE company_id = %s
                ORDER BY forecast_month ASC, product_name ASC
                """,
                (company_id,),
            )
            bpm = cur.fetchall()
            by_product = []
            for r in bpm:
                by_product.append(
                    {
                        "product_name": r["product_name"],
                        "forecast_month": r["forecast_month"].isoformat()
                        if hasattr(r["forecast_month"], "isoformat")
                        else str(r["forecast_month"]),
                        "units_forecasted": r["units_forecasted"],
                        "final_price": _json_num(r["final_price"]),
                        "final_cogs": _json_num(r["final_cogs"]),
                        "cm_per_unit": _json_num(r["cm_per_unit"]),
                        "cm_ratio_percent": _json_num(r["cm_ratio_percent"]),
                    }
                )

            return {"columns": cols, "rows": rows_out, "by_product_month": by_product}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/financial-model/cashflow/{company_id}")
def financial_model_cashflow(company_id: int, conn=Depends(get_db)):
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT forecast_month, revenue, net_profit
                FROM view_financial_health
                WHERE company_id = %s
                ORDER BY forecast_month ASC
                """,
                (company_id,),
            )
            fh = cur.fetchall()

            cur.execute(
                """
                SELECT DATE_TRUNC('month', expense_date)::DATE AS m, SUM(amount) AS capex
                FROM one_off_expenses
                WHERE company_id = %s
                GROUP BY 1
                ORDER BY 1 ASC
                """,
                (company_id,),
            )
            capex_rows = {r["m"].isoformat() if hasattr(r["m"], "isoformat") else str(r["m"]): float(r["capex"] or 0) for r in cur.fetchall()}

            cols = []
            op_proxy = []
            capex_cells = []
            net_cf_proxy = []
            for r in fh:
                key = r["forecast_month"].isoformat() if hasattr(r["forecast_month"], "isoformat") else str(r["forecast_month"])
                cols.append(key)
                npv = float(r["net_profit"] or 0)
                op_proxy.append(npv)
                cx = capex_rows.get(key, 0.0)
                capex_cells.append(cx)
                net_cf_proxy.append(npv - cx)

            runway = _runway_payload(company_id, cur)

            rows_out = [
                {
                    "key": "operating_cash_proxy",
                    "label": "Operating cash proxy (net profit accrual)",
                    "cells": op_proxy,
                },
                {"key": "capex", "label": "CAPEX (one-off expenses in month)", "cells": capex_cells},
                {
                    "key": "net_cash_flow_proxy",
                    "label": "Net cash movement proxy (operating − CAPEX)",
                    "cells": net_cf_proxy,
                },
            ]

            return {"columns": cols, "rows": rows_out, "runway": runway}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/financial-model/balance-sheet/{company_id}")
def financial_model_balance_sheet(company_id: int, conn=Depends(get_db)):
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT balance_date, total_cash
                FROM cash_balances
                WHERE company_id = %s
                ORDER BY balance_date DESC
                LIMIT 1
                """,
                (company_id,),
            )
            cash_row = cur.fetchone()
            as_of = cash_row["balance_date"].isoformat() if cash_row and cash_row.get("balance_date") else None
            cash_amt = _json_num(cash_row["total_cash"]) if cash_row else None

            lines = [
                {"section": "assets", "key": "cash", "label": "Cash and cash equivalents", "amount": cash_amt},
                {"section": "assets", "key": "receivables", "label": "Accounts receivable", "amount": None, "note": "Connect Xero / accounting sync"},
                {"section": "assets", "key": "inventory", "label": "Inventory", "amount": None, "note": "Connect Xero / accounting sync"},
                {"section": "assets", "key": "other_current_assets", "label": "Other current assets", "amount": None},
                {"section": "assets", "key": "non_current_assets", "label": "Non-current assets", "amount": None},
                {"section": "liabilities", "key": "payables", "label": "Accounts payable", "amount": None, "note": "Connect Xero / accounting sync"},
                {"section": "liabilities", "key": "current_liabilities", "label": "Other current liabilities", "amount": None},
                {"section": "liabilities", "key": "non_current_liabilities", "label": "Non-current liabilities", "amount": None},
                {"section": "equity", "key": "equity", "label": "Equity (plug)", "amount": None},
            ]

            ratios = {
                "net_working_capital": None,
                "current_ratio": None,
                "quick_ratio": None,
                "asset_turnover": None,
                "roa": None,
                "note": "Ratios require full balance sheet lines from accounting integration.",
            }

            return {"as_of": as_of, "lines": lines, "ratios": ratios}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/financial-model/kpis/{company_id}")
def financial_model_kpis(company_id: int, conn=Depends(get_db)):
    try:
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
            m = cur.fetchone()
            raw_metrics = {k: _json_num(v) for k, v in m.items()} if m else {}

            cur.execute(
                """
                SELECT gross_margin_percent, revenue, net_profit
                FROM view_financial_health
                WHERE company_id = %s
                ORDER BY forecast_month DESC
                LIMIT 1
                """,
                (company_id,),
            )
            latest_pl = cur.fetchone()

            gm_pct = float(latest_pl["gross_margin_percent"]) if latest_pl and latest_pl.get("gross_margin_percent") is not None else None
            mrr = raw_metrics.get("mrr")
            arr = (mrr * 12) if mrr is not None else None

            nrr = None
            if (
                raw_metrics.get("starting_mrr") is not None
                and float(raw_metrics["starting_mrr"]) > 0
            ):
                sm = float(raw_metrics["starting_mrr"])
                exp = float(raw_metrics.get("expansion_mrr") or 0)
                con = float(raw_metrics.get("contraction_mrr") or 0)
                chm = float(raw_metrics.get("churned_mrr") or 0)
                nrr = round(((sm + exp - chm - con) / sm) * 100, 2)

            cac_computed = None
            if raw_metrics.get("sm_spend") is not None and raw_metrics.get("new_customers"):
                nc = int(raw_metrics["new_customers"])
                if nc > 0:
                    cac_computed = round(float(raw_metrics["sm_spend"]) / nc, 2)

            cac_for_ratio = cac_computed
            if cac_for_ratio is None and raw_metrics.get("cac"):
                try:
                    cac_for_ratio = float(raw_metrics["cac"])
                except (TypeError, ValueError):
                    cac_for_ratio = None

            ltv_cac = None
            if raw_metrics.get("ltv") is not None and cac_for_ratio and cac_for_ratio > 0:
                ltv_cac = round(float(raw_metrics["ltv"]) / cac_for_ratio, 2)

            cac_payback_months = None
            payback_cac = cac_computed
            if payback_cac is None and raw_metrics.get("cac"):
                try:
                    payback_cac = float(raw_metrics["cac"])
                except (TypeError, ValueError):
                    payback_cac = None
            if payback_cac and payback_cac > 0 and mrr is not None and gm_pct is not None:
                monthly_rev_per_customer = float(mrr)
                margin_f = gm_pct / 100.0
                denom = monthly_rev_per_customer * margin_f
                if denom > 0:
                    cac_payback_months = round(payback_cac / denom, 2)

            monthly_churn_pct = None
            if raw_metrics.get("customers_start_of_month") and int(raw_metrics["customers_start_of_month"]) > 0:
                lost = int(raw_metrics.get("customers_lost") or 0)
                monthly_churn_pct = round((lost / int(raw_metrics["customers_start_of_month"])) * 100, 4)

            computed = {
                "mrr": mrr,
                "arr": arr,
                "nrr_percent": nrr,
                "cac_from_inputs": cac_computed,
                "cac_stored": raw_metrics.get("cac"),
                "ltv_cac_ratio": ltv_cac,
                "cac_payback_months": cac_payback_months,
                "monthly_churn_percent": monthly_churn_pct,
                "churn_rate_stored": raw_metrics.get("churn_rate"),
                "ltv_stored": raw_metrics.get("ltv"),
                "latest_forecast_gross_margin_percent": _json_num(latest_pl["gross_margin_percent"]) if latest_pl else None,
            }

            return {"metrics_row": raw_metrics, "computed": computed}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/projects/list/{company_id}")
def get_projects(company_id: int, conn=Depends(get_db)):
    try:
        with conn.cursor() as cur:
            cur.execute('SELECT * FROM projects WHERE company_id = %s ORDER BY id DESC', (company_id,))
            return cur.fetchall()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# --- STATIC FILES (FRONTEND) ---
# Must run before the catch-all StaticFiles mount so `/app/*.html` is not eaten by static 404s.
@app.api_route("/app/{stub}.html", methods=["GET", "HEAD"])
async def app_prefixed_html_alias(stub: str):
    """Serve dashboard at ``/app/index.html``; redirect other ``/app/*.html`` to root ``/*.html``.

    Relative links from ``/app/`` resolve to ``/app/foo.html``; static HTML lives at ``/foo.html``.
    """
    filename = f"{stub}.html"
    if stub == "index":
        path = os.path.join(frontend_path, "app", "index.html")
        if not os.path.isfile(path):
            raise HTTPException(status_code=404, detail="Not Found")
        return FileResponse(path, media_type="text/html")
    candidate = os.path.join(frontend_path, filename)
    if os.path.isfile(candidate):
        return RedirectResponse(url=f"/{filename}", status_code=307)
    raise HTTPException(status_code=404, detail="Not Found")


app.mount("/", StaticFiles(directory=frontend_path, html=True), name="frontend")


if __name__ == "__main__":
    import uvicorn

    port = int(os.getenv("PORT", "9090"))
    uvicorn.run("main:app", host="127.0.0.1", port=port)