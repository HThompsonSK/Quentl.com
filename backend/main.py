import os
from contextlib import contextmanager
from fastapi import FastAPI, HTTPException, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from typing import Optional
from datetime import date
import psycopg2
from psycopg2.extras import RealDictCursor
from dotenv import load_dotenv

# Load Environment Variables
load_dotenv()

app = FastAPI()

# Enable CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- DATABASE CONNECTION DEPENDENCY ---
def get_db():
    """Yields a database connection and handles cleanup."""
    conn = psycopg2.connect(
        os.getenv("DATABASE_URL"), 
        cursor_factory=RealDictCursor
    )
    try:
        yield conn
    finally:
        conn.close()

# --- PYDANTIC MODELS ---

from typing import List

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
    mau: int
    cac: float
    churn_rate: float
    ltv: float
    revenue_per_employee: float

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

# --- 5. SOLVENCY ENGINE (PREDICTIVE LOGIC) ---

@app.get("/api/runway/{company_id}")
def get_runway(company_id: int, conn=Depends(get_db)):
    try:
        with conn.cursor() as cur:
            # 1. Get Starting Cash (most recent snapshot)
            cur.execute("""
                SELECT total_cash 
                FROM cash_balances 
                WHERE company_id = %s 
                ORDER BY balance_date DESC 
                LIMIT 1
            """, (company_id,))
            cash_row = cur.fetchone()
            starting_cash = float(cash_row['total_cash']) if cash_row else 0.0

            # 2. Calculate Burn Rate (Sum of fixed expenses)
            cur.execute("""
                SELECT SUM(amount) as burn_rate 
                FROM fixed_expenses 
                WHERE company_id = %s
            """, (company_id,))
            burn_row = cur.fetchone()
            burn_rate = float(burn_row['burn_rate']) if burn_row and burn_row['burn_rate'] else 0.0

            # 3. Calculate Expected Cash from Pipeline (Weighted Value)
            cur.execute("""
                SELECT SUM(contract_value * (probability / 100.0)) as expected_cash 
                FROM leads 
                WHERE company_id = %s
            """, (company_id,))
            expected_row = cur.fetchone()
            expected_cash = float(expected_row['expected_cash']) if expected_row and expected_row['expected_cash'] else 0.0

            # 4. Calculate CAPEX (Sum of one-off expenses)
            cur.execute("""
                SELECT SUM(amount) as total_capex 
                FROM one_off_expenses 
                WHERE company_id = %s
            """, (company_id,))
            capex_row = cur.fetchone()
            capex = float(capex_row['total_capex']) if capex_row and capex_row['total_capex'] else 0.0

            # 5. The Math: Calculate Runway
            if burn_rate == 0:
                estimated_months_left = None
                status_message = "Infinite runway - no expenses logged"
                status_color = "Green"
            else:
                estimated_months_left = (starting_cash + expected_cash - capex) / burn_rate
                status_message = f"{estimated_months_left:.1f} months"
                
                # Determine Health Status Color
                if estimated_months_left >= 6:
                    status_color = "Green"
                elif estimated_months_left >= 3:
                    status_color = "Yellow"
                else:
                    status_color = "Red"

            # Return the complete JSON payload
            return {
                "company_id": company_id,
                "starting_cash": starting_cash,
                "burn_rate": burn_rate,
                "expected_cash": expected_cash,
                "capex": capex,
                "estimated_months_left": estimated_months_left,
                "status_message": status_message,
                "status_color": status_color
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
            return result if result else {} # Return empty dict if no data yet
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/metrics")
def add_metrics(item: MetricCreate, conn=Depends(get_db)):
    try:
        with conn.cursor() as cur:
            # Insert, or update if that month already exists
            cur.execute("""
                INSERT INTO monthly_metrics (company_id, month_date, mau, cac, churn_rate, ltv, revenue_per_employee)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (company_id, month_date) 
                DO UPDATE SET 
                    mau = EXCLUDED.mau, 
                    cac = EXCLUDED.cac, 
                    churn_rate = EXCLUDED.churn_rate, 
                    ltv = EXCLUDED.ltv, 
                    revenue_per_employee = EXCLUDED.revenue_per_employee
                RETURNING *
            """, (item.company_id, item.month_date, item.mau, item.cac, item.churn_rate, item.ltv, item.revenue_per_employee))
            result = cur.fetchone()
            conn.commit()
            return result
    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail=str(e))

# --- PYTHON ENDPOINT

@app.get("/api/projects/list/{company_id}")
def get_projects(company_id: int, conn=Depends(get_db)):
    try:
        with conn.cursor() as cur:
            cur.execute('SELECT * FROM projects WHERE company_id = %s ORDER BY id DESC', (company_id,))
            return cur.fetchall()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# --- STATIC FILES (FRONTEND) ---
frontend_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "../frontend"))
app.mount("/", StaticFiles(directory=frontend_path, html=True), name="frontend")


if __name__ == "__main__":
    import uvicorn

    port = int(os.getenv("PORT", "9090"))
    uvicorn.run("main:app", host="127.0.0.1", port=port)