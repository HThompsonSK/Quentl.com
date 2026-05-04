-- Accounting integrations: normalized actuals, linking, OAuth-ready connections.
-- Run after base schema. Safe to re-run where IF NOT EXISTS / IF NOT EXISTS patterns apply.

-- ---------------------------------------------------------------------------
-- Leads (pipeline) — referenced by app but may be missing from early schemas
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS leads (
    id SERIAL PRIMARY KEY,
    company_id INT NOT NULL,
    client_name VARCHAR(255) NOT NULL,
    contract_value DECIMAL(19, 4) NOT NULL DEFAULT 0,
    probability INT NOT NULL DEFAULT 0,
    expected_close_date DATE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT fk_leads_company FOREIGN KEY (company_id) REFERENCES companies(id) ON DELETE CASCADE
);

-- ---------------------------------------------------------------------------
-- api_connections: QBO realm + uniqueness for upsert
-- ---------------------------------------------------------------------------
ALTER TABLE api_connections ADD COLUMN IF NOT EXISTS realm_id VARCHAR(255);
COMMENT ON COLUMN api_connections.tenant_id IS 'Xero organisation (tenant) ID';
COMMENT ON COLUMN api_connections.realm_id IS 'QuickBooks Online company (realm) ID';

CREATE UNIQUE INDEX IF NOT EXISTS uq_api_connections_company_service
    ON api_connections (company_id, service_name);

-- ---------------------------------------------------------------------------
-- QuickBooks mapping parity (products / fixed expenses)
-- ---------------------------------------------------------------------------
ALTER TABLE products ADD COLUMN IF NOT EXISTS qbo_item_id VARCHAR(255);
ALTER TABLE products ADD COLUMN IF NOT EXISTS qbo_income_account_ref VARCHAR(255);

ALTER TABLE fixed_expenses ADD COLUMN IF NOT EXISTS qbo_account_ref VARCHAR(255);

-- ---------------------------------------------------------------------------
-- Normalized accounting lines (accrual vs cash; idempotent per provider)
-- basis: accrual = invoice recognition (issued), cash = payment received
-- line_kind: invoice_total | invoice_payment | qbo_invoice | qbo_payment
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS accounting_actual_lines (
    id SERIAL PRIMARY KEY,
    company_id INT NOT NULL,
    connection_id INT REFERENCES api_connections(id) ON DELETE SET NULL,
    provider VARCHAR(32) NOT NULL,
    basis VARCHAR(16) NOT NULL,
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
    CONSTRAINT fk_aal_company FOREIGN KEY (company_id) REFERENCES companies(id) ON DELETE CASCADE,
    CONSTRAINT uq_aal_idempotency UNIQUE (company_id, idempotency_key)
);

CREATE INDEX IF NOT EXISTS idx_aal_company_date ON accounting_actual_lines (company_id, posted_date DESC);
CREATE INDEX IF NOT EXISTS idx_aal_company_basis ON accounting_actual_lines (company_id, basis);

-- ---------------------------------------------------------------------------
-- Link accounting contacts to pipeline leads (auto-match + manual override)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS lead_accounting_links (
    id SERIAL PRIMARY KEY,
    company_id INT NOT NULL,
    lead_id INT NOT NULL,
    provider VARCHAR(32) NOT NULL,
    external_contact_id VARCHAR(255) NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT fk_lal_company FOREIGN KEY (company_id) REFERENCES companies(id) ON DELETE CASCADE,
    CONSTRAINT fk_lal_lead FOREIGN KEY (lead_id) REFERENCES leads(id) ON DELETE CASCADE,
    CONSTRAINT uq_lal_contact UNIQUE (company_id, provider, external_contact_id)
);

CREATE INDEX IF NOT EXISTS idx_lal_lead ON lead_accounting_links (lead_id);

-- ---------------------------------------------------------------------------
-- Repair broken views from legacy schedma.sql (if present)
-- ---------------------------------------------------------------------------
DROP VIEW IF EXISTS view_financial_health;
DROP VIEW IF EXISTS view_monthly_summary;

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

CREATE OR REPLACE VIEW view_financial_health AS
WITH monthly_data AS (
    SELECT
        f.company_id,
        f.forecast_month,
        SUM(f.total_revenue) AS revenue,
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
)
SELECT
    md.company_id,
    md.forecast_month,
    md.revenue,
    md.gross_profit,
    COALESCE(mf.total_bills, 0) AS fixed_costs,
    (md.gross_profit - COALESCE(mf.total_bills, 0)) AS net_profit,
    ROUND((md.gross_profit / md.safe_revenue) * 100, 2) AS gross_margin_percent,
    CASE
        WHEN md.gross_profit > 0 THEN
            ROUND(COALESCE(mf.total_bills, 0) / (md.gross_profit / md.safe_revenue), 2)
        ELSE 0
    END AS revenue_needed_to_break_even
FROM monthly_data md
LEFT JOIN monthly_fixed mf ON md.company_id = mf.company_id;
