-- ==========================================
-- 1. FOUNDATION TABLES (Multi-Tenancy)
-- ==========================================

-- The "Tenants" - Each company is isolated here
CREATE TABLE IF NOT EXISTS companies (
    id SERIAL PRIMARY KEY,
    name VARCHAR(255) NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- The Users - Linked to a specific company
CREATE TABLE IF NOT EXISTS users (
    id SERIAL PRIMARY KEY,
    company_id INT NOT NULL,
    email VARCHAR(255) UNIQUE NOT NULL,
    password_hash VARCHAR(255) NOT NULL, -- Store hashed passwords, never plain text
    full_name VARCHAR(100),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    
    CONSTRAINT fk_company_user FOREIGN KEY (company_id) REFERENCES companies (id) ON DELETE CASCADE
);

-- ==========================================
-- 2. CORE DATA TABLES (The "Inputs")
-- ==========================================

-- Products / Service Lines (The "Master Data")
CREATE TABLE IF NOT EXISTS products (
    id SERIAL PRIMARY KEY,
    company_id INT NOT NULL,
    name VARCHAR(255) NOT NULL,      -- e.g. "Strawberry Jam"
    sku VARCHAR(100),                -- e.g. "JAM-001"
    
    -- Financial Defaults (The Baseline)
    default_sales_price DECIMAL(19, 4) DEFAULT 0.0000, 
    default_cogs DECIMAL(19, 4) DEFAULT 0.0000, -- Cost of Goods Sold (Materials)
    
    -- Xero Mapping
    xero_item_id VARCHAR(255),       -- ID inside Xero
    xero_sales_account VARCHAR(50),  -- e.g. "200"
    -- QuickBooks Online mapping
    qbo_item_id VARCHAR(255),
    qbo_income_account_ref VARCHAR(255),
    
    is_active BOOLEAN DEFAULT TRUE,
    
    CONSTRAINT fk_company_product FOREIGN KEY (company_id) REFERENCES companies (id) ON DELETE CASCADE
);

-- Fixed Expenses (OpEx) - Costs that happen regardless of sales
CREATE TABLE IF NOT EXISTS fixed_expenses (
    id SERIAL PRIMARY KEY,
    company_id INT NOT NULL,
    name VARCHAR(255) NOT NULL,      -- e.g. "Rent", "Insurance"
    amount DECIMAL(19, 4) DEFAULT 0.0000,
    
    -- Cashflow Timing Logic
    frequency VARCHAR(20) DEFAULT 'monthly', -- 'monthly', 'quarterly', 'annually'
    payment_day INT DEFAULT 1,       -- Day of month cash leaves
    
    -- Xero Mapping
    xero_account_code VARCHAR(50),   -- e.g. "400" (Advertising)
    qbo_account_ref VARCHAR(255),
    
    CONSTRAINT fk_company_expense FOREIGN KEY (company_id) REFERENCES companies (id) ON DELETE CASCADE
);

-- ==========================================
-- 3. THE "HUMAN BESPOKE" LOGIC
-- ==========================================

-- Forecast Entries - Where the user inputs their monthly predictions
CREATE TABLE IF NOT EXISTS forecast_entries (
    id SERIAL PRIMARY KEY,
    company_id INT NOT NULL,
    product_id INT NOT NULL,
    forecast_month DATE NOT NULL,    -- Always store as 1st of month (e.g., 2025-01-01)
    
    -- The Prediction
    units_forecasted INT DEFAULT 0,
    
    -- The "Bespoke" Overrides (What-If Scenarios)
    -- If NULL, the system uses the default from the products table
    price_override DECIMAL(19, 4) DEFAULT NULL,
    cogs_override DECIMAL(19, 4) DEFAULT NULL,
    
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT fk_company_fc FOREIGN KEY (company_id) REFERENCES companies (id) ON DELETE CASCADE,
    CONSTRAINT fk_product_fc FOREIGN KEY (product_id) REFERENCES products (id) ON DELETE CASCADE,
    
    -- Prevent duplicate entries for the same product in the same month
    CONSTRAINT unique_forecast UNIQUE (company_id, product_id, forecast_month)
);

-- ==========================================
-- 3b. CRM / PIPELINE
-- ==========================================

CREATE TABLE IF NOT EXISTS leads (
    id SERIAL PRIMARY KEY,
    company_id INT NOT NULL,
    client_name VARCHAR(255) NOT NULL,
    contract_value DECIMAL(19, 4) NOT NULL DEFAULT 0,
    probability INT NOT NULL DEFAULT 0,
    expected_close_date DATE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT fk_leads_company FOREIGN KEY (company_id) REFERENCES companies (id) ON DELETE CASCADE
);

-- ==========================================
-- 4. INTEGRATIONS
-- ==========================================

-- Store OAuth tokens (encrypt at rest in application code)
CREATE TABLE IF NOT EXISTS api_connections (
    id SERIAL PRIMARY KEY,
    company_id INT NOT NULL,
    service_name VARCHAR(50) NOT NULL, -- 'xero', 'quickbooks'
    access_token TEXT NOT NULL,
    refresh_token TEXT NOT NULL,
    tenant_id VARCHAR(255),            -- Xero organisation ID
    realm_id VARCHAR(255),            -- QuickBooks Online realm (company) ID
    expires_at TIMESTAMP,
    
    CONSTRAINT fk_company_api FOREIGN KEY (company_id) REFERENCES companies (id) ON DELETE CASCADE,
    CONSTRAINT uq_api_connections_company_service UNIQUE (company_id, service_name)
);

-- Normalized lines from Xero / QBO (accrual vs cash; idempotent ingest)
CREATE TABLE IF NOT EXISTS accounting_actual_lines (
    id SERIAL PRIMARY KEY,
    company_id INT NOT NULL,
    connection_id INT REFERENCES api_connections(id) ON DELETE SET NULL,
    provider VARCHAR(32) NOT NULL,
    basis VARCHAR(16) NOT NULL,         -- 'accrual' | 'cash'
    line_kind VARCHAR(40) NOT NULL,
    external_invoice_id VARCHAR(255),
    external_payment_id VARCHAR(255),
    external_contact_id VARCHAR(255),
    contact_name TEXT,
    amount DECIMAL(19, 4) NOT NULL,
    currency CHAR(3) NOT NULL DEFAULT 'GBP',
    posted_date DATE NOT NULL,
    lead_id INT REFERENCES leads(id) ON DELETE SET NULL,
    idempotency_key VARCHAR(512) NOT NULL,
    synced_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT fk_aal_company FOREIGN KEY (company_id) REFERENCES companies (id) ON DELETE CASCADE,
    CONSTRAINT uq_aal_idempotency UNIQUE (company_id, idempotency_key)
);

CREATE INDEX IF NOT EXISTS idx_aal_company_date ON accounting_actual_lines (company_id, posted_date DESC);

-- Map provider contact IDs to pipeline leads (for auto-attach on sync)
CREATE TABLE IF NOT EXISTS lead_accounting_links (
    id SERIAL PRIMARY KEY,
    company_id INT NOT NULL,
    lead_id INT NOT NULL,
    provider VARCHAR(32) NOT NULL,
    external_contact_id VARCHAR(255) NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT fk_lal_company FOREIGN KEY (company_id) REFERENCES companies (id) ON DELETE CASCADE,
    CONSTRAINT fk_lal_lead FOREIGN KEY (lead_id) REFERENCES leads(id) ON DELETE CASCADE,
    CONSTRAINT uq_lal_contact UNIQUE (company_id, provider, external_contact_id)
);

-- ==========================================
-- 5. THE "MAGIC" CALCULATION ENGINE (Views)
-- ==========================================

-- VIEW 1: Detailed Product Financials
-- This calculates Revenue, Costs, and Margin automatically per row.
-- It handles the logic: "Use Override Price if exists, otherwise use Default Price"
CREATE OR REPLACE VIEW view_forecast_details AS
SELECT 
    f.id AS forecast_id,
    f.company_id,
    f.forecast_month,
    p.name AS product_name,
    f.units_forecasted,
    
    -- 1. Determine Final Price (Override vs Default)
    COALESCE(f.price_override, p.default_sales_price) AS final_price,
    
    -- 2. Determine Final COGS (Override vs Default)
    COALESCE(f.cogs_override, p.default_cogs) AS final_cogs,
    
    -- 3. Calculate Total Revenue
    (f.units_forecasted * COALESCE(f.price_override, p.default_sales_price)) AS total_revenue,
    
    -- 4. Calculate Total Variable Costs
    (f.units_forecasted * COALESCE(f.cogs_override, p.default_cogs)) AS total_variable_cost,
    
    -- 5. Calculate Gross Profit (Contribution)
    (f.units_forecasted * COALESCE(f.price_override, p.default_sales_price)) - 
    (f.units_forecasted * COALESCE(f.cogs_override, p.default_cogs)) AS gross_profit

FROM forecast_entries f
JOIN products p ON f.product_id = p.id;

-- VIEW 2: Monthly Financial Summary
-- This groups everything by month to show the client "Am I afloat?"
CREATE OR REPLACE VIEW view_monthly_summary AS
SELECT
    f.company_id,
    f.forecast_month,
    SUM(f.total_revenue) AS monthly_revenue,
    SUM(f.total_variable_cost) AS monthly_cogs,
    (SELECT COALESCE(SUM(amount), 0) FROM fixed_expenses fe WHERE fe.company_id = f.company_id) AS monthly_fixed_costs,
    SUM(f.gross_profit) - (SELECT COALESCE(SUM(amount), 0) FROM fixed_expenses fe2 WHERE fe2.company_id = f.company_id) AS net_profit
FROM view_forecast_details f
GROUP BY f.company_id, f.forecast_month;

-- ==========================================
-- 5b. MONTHLY FOUNDER KPI INPUTS (optional; used by /api/metrics and financial model KPIs)
-- ==========================================
CREATE TABLE IF NOT EXISTS monthly_metrics (
    id SERIAL PRIMARY KEY,
    company_id INT NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
    month_date DATE NOT NULL,
    mau INT NOT NULL DEFAULT 0,
    cac DECIMAL(19, 4) NOT NULL DEFAULT 0,
    churn_rate DECIMAL(19, 6) NOT NULL DEFAULT 0,
    ltv DECIMAL(19, 4) NOT NULL DEFAULT 0,
    revenue_per_employee DECIMAL(19, 4) NOT NULL DEFAULT 0,
    mrr DECIMAL(19, 4),
    expansion_mrr DECIMAL(19, 4),
    contraction_mrr DECIMAL(19, 4),
    churned_mrr DECIMAL(19, 4),
    starting_mrr DECIMAL(19, 4),
    new_customers INT,
    sm_spend DECIMAL(19, 4),
    customers_start_of_month INT,
    customers_lost INT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uq_monthly_metrics_company_month UNIQUE (company_id, month_date)
);

CREATE INDEX IF NOT EXISTS idx_monthly_metrics_company_month
    ON monthly_metrics (company_id, month_date DESC);

-- ==========================================
-- 6. ADVANCED ANALYTICS (The "Stay Afloat" Engine)
-- ==========================================

CREATE OR REPLACE VIEW view_financial_health AS
WITH monthly_data AS (
    SELECT
        f.company_id,
        f.forecast_month,
        SUM(f.total_revenue) AS revenue,
        SUM(f.total_variable_cost) AS variable_costs,
        SUM(f.gross_profit) AS gross_profit,
        NULLIF(SUM(f.total_revenue), 0) AS safe_revenue
    FROM view_forecast_details f
    GROUP BY f.company_id, f.forecast_month
),
monthly_fixed AS (
    SELECT
        company_id,
        SUM(amount) AS total_bills
    FROM fixed_expenses
    WHERE frequency = 'monthly'
    GROUP BY company_id
),
pipeline AS (
    SELECT
        company_id,
        COALESCE(SUM(contract_value * (probability / 100.0)), 0) AS weighted_pipeline_value
    FROM leads
    GROUP BY company_id
),
core AS (
    SELECT
        md.company_id,
        md.forecast_month,
        md.revenue,
        md.variable_costs,
        md.gross_profit,
        COALESCE(mf.total_bills, 0) AS fixed_costs,
        (md.gross_profit - COALESCE(mf.total_bills, 0)) AS net_profit,
        ROUND((md.gross_profit / md.safe_revenue) * 100, 2) AS gross_margin_percent,
        CASE
            WHEN md.gross_profit > 0 THEN
                ROUND(COALESCE(mf.total_bills, 0) / (md.gross_profit / md.safe_revenue), 2)
            ELSE 0
        END AS revenue_needed_to_break_even,
        COALESCE(pl.weighted_pipeline_value, 0) AS weighted_pipeline_value
    FROM monthly_data md
    LEFT JOIN monthly_fixed mf ON md.company_id = mf.company_id
    LEFT JOIN pipeline pl ON pl.company_id = md.company_id
)
SELECT
    c.company_id,
    c.forecast_month,
    c.revenue,
    c.variable_costs,
    c.gross_profit,
    c.fixed_costs,
    c.net_profit,
    c.gross_margin_percent,
    c.revenue_needed_to_break_even,
    c.weighted_pipeline_value,
    SUM(c.net_profit) OVER (
        PARTITION BY c.company_id
        ORDER BY c.forecast_month
        ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
    ) AS ytd_net_profit
FROM core c;
