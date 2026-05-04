-- Financial model: founder KPI storage + dashboard view fields (ytd_net_profit, weighted_pipeline_value).
-- Run after 001_accounting_integration.sql (or any schema that defines view_forecast_details, leads, fixed_expenses).

-- ---------------------------------------------------------------------------
-- monthly_metrics — optional monthly KPI inputs (POST /api/metrics)
-- ---------------------------------------------------------------------------
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

-- Extend table if an older partial definition existed
ALTER TABLE monthly_metrics ADD COLUMN IF NOT EXISTS mrr DECIMAL(19, 4);
ALTER TABLE monthly_metrics ADD COLUMN IF NOT EXISTS expansion_mrr DECIMAL(19, 4);
ALTER TABLE monthly_metrics ADD COLUMN IF NOT EXISTS contraction_mrr DECIMAL(19, 4);
ALTER TABLE monthly_metrics ADD COLUMN IF NOT EXISTS churned_mrr DECIMAL(19, 4);
ALTER TABLE monthly_metrics ADD COLUMN IF NOT EXISTS starting_mrr DECIMAL(19, 4);
ALTER TABLE monthly_metrics ADD COLUMN IF NOT EXISTS new_customers INT;
ALTER TABLE monthly_metrics ADD COLUMN IF NOT EXISTS sm_spend DECIMAL(19, 4);
ALTER TABLE monthly_metrics ADD COLUMN IF NOT EXISTS customers_start_of_month INT;
ALTER TABLE monthly_metrics ADD COLUMN IF NOT EXISTS customers_lost INT;
ALTER TABLE monthly_metrics ADD COLUMN IF NOT EXISTS created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP;
ALTER TABLE monthly_metrics ADD COLUMN IF NOT EXISTS updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP;

-- ---------------------------------------------------------------------------
-- view_financial_health — add weighted_pipeline_value + ytd_net_profit for dashboard
-- ---------------------------------------------------------------------------
DROP VIEW IF EXISTS view_financial_health;

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
