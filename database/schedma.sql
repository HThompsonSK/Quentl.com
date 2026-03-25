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
-- 4. INTEGRATIONS
-- ==========================================

-- Store Xero Tokens securely
CREATE TABLE IF NOT EXISTS api_connections (
    id SERIAL PRIMARY KEY,
    company_id INT NOT NULL,
    service_name VARCHAR(50) NOT NULL, -- 'xero', 'quickbooks'
    access_token TEXT NOT NULL,        -- Encrypted in app logic
    refresh_token TEXT NOT NULL,       -- Encrypted in app logic
    tenant_id VARCHAR(255),            -- Xero Organization ID
    expires_at TIMESTAMP,
    
    CONSTRAINT fk_company_api FOREIGN KEY (company_id) REFERENCES companies (id) ON DELETE CASCADE
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
    
    -- Sum of all product revenues
    SUM(f.total_revenue) AS monthly_revenue,
    
    -- Sum of all product costs (COGS)
    SUM(f.total_variable_cost) AS monthly_cogs,
    
    -- Sum of Fixed Expenses (Rent, etc.)
    -- Note: This assumes fixed_expenses are monthly. 
    -- (In a full app, you'd add logic here for quarterly payments)
    (SELECT COALESCE(SUM(amount), 0) FROM fixed_expenses WHERE company_id = f.company_id) AS monthly_fixed_costs,
    
    -- The Bottom Line: (Revenue - COGS - Fixed Costs)
    SUM(f.gross_profit) - (SELECT COALESCE(SUM(amount), 0) FROM fixed_expenses WHERE company_id = f.company_id) AS net_profit

    -- ==========================================
-- 6. ADVANCED ANALYTICS (The "Stay Afloat" Engine)
-- ==========================================

CREATE OR REPLACE VIEW view_financial_health AS
WITH monthly_data AS (
    -- 1. Aggregate the forecast data (Revenue & Margin)
    SELECT
        f.company_id,
        f.forecast_month,
        SUM(f.total_revenue) AS revenue,
        SUM(f.gross_profit) AS gross_profit,
        -- Avoid division by zero errors later
        NULLIF(SUM(f.total_revenue), 0) AS safe_revenue
    FROM view_forecast_details f
    GROUP BY f.company_id, f.forecast_month
),
monthly_fixed AS (
    -- 2. Calculate Total Fixed Costs (Bills/Salaries) per company
    -- (In a real app, you would filter this by date, e.g. active expenses only)
    SELECT 
        company_id, 
        SUM(amount) AS total_bills
    FROM fixed_expenses
    WHERE frequency = 'monthly' -- Simple filter for now
    GROUP BY company_id
)
SELECT
    md.company_id,
    md.forecast_month,
    
    -- A. The Basics
    md.revenue,
    md.gross_profit,
    COALESCE(mf.total_bills, 0) AS fixed_costs,
    
    -- B. Net Profit (The Result)
    (md.gross_profit - COALESCE(mf.total_bills, 0)) AS net_profit,
    
    -- C. Margin Percentage (Efficiency)
    -- "For every £1 I earn, I keep X pence"
    ROUND((md.gross_profit / md.safe_revenue) * 100, 2) AS gross_margin_percent,
    
    -- D. Break-Even Point (The "Stay Afloat" Number)
    -- Formula: Fixed Costs / Gross Margin %
    -- This tells them exactly how much revenue they NEED to hit 0 profit.
    CASE 
        WHEN md.gross_profit > 0 THEN 
            ROUND(COALESCE(mf.total_bills, 0) / (md.gross_profit / md.safe_revenue), 2)
        ELSE 
            0 -- If margin is negative, break-even is impossible to calculate simply
    END AS revenue_needed_to_break_even

FROM monthly_data md
LEFT JOIN monthly_fixed mf ON md.company_id = mf.company_id;

FROM view_forecast_details f
GROUP BY f.company_id, f.forecast_month;
