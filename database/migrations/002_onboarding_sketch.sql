-- Conversational onboarding + calibration sketch (run after base schema + 001).
-- Aligns with companies(id) and post-connect sync (Xero / QBO via api_connections).

-- ---------------------------------------------------------------------------
-- Session: one interview attempt per company (or many; no unique constraint).
-- integration_snapshot: optional JSON capture of connected providers at start,
--   e.g. {"xero": true, "quickbooks": false} from api_connections.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS onboarding_sessions (
    id SERIAL PRIMARY KEY,
    company_id INT NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
    user_id INT REFERENCES users(id) ON DELETE SET NULL,
    status VARCHAR(32) NOT NULL DEFAULT 'in_progress',
    current_step_key VARCHAR(64),
    integration_snapshot JSONB NOT NULL DEFAULT '{}',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    completed_at TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_onboarding_sessions_company
    ON onboarding_sessions (company_id, created_at DESC);

COMMENT ON TABLE onboarding_sessions IS 'Layer-2 interview instance; ties messages and optional calibration runs to a company.';

-- ---------------------------------------------------------------------------
-- Chat transcript + optional per-turn structured extraction (LLM delta).
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS onboarding_messages (
    id SERIAL PRIMARY KEY,
    session_id INT NOT NULL REFERENCES onboarding_sessions(id) ON DELETE CASCADE,
    role VARCHAR(16) NOT NULL,
    content TEXT NOT NULL,
    structured_patch JSONB,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT chk_onboarding_message_role CHECK (role IN ('user', 'assistant', 'system'))
);

CREATE INDEX IF NOT EXISTS idx_onboarding_messages_session
    ON onboarding_messages (session_id, id);

COMMENT ON COLUMN onboarding_messages.structured_patch IS 'Incremental JSON from model/tool output merged server-side into company_onboarding_profile.';

-- ---------------------------------------------------------------------------
-- Normalised interview output (Layer-2 → Layer-3 input). One active row per company.
-- data: e.g. identity, comms_tone, business_model, revenue_streams[], goals[], pain_points[], stakeholders[]
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS company_onboarding_profiles (
    id SERIAL PRIMARY KEY,
    company_id INT NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
    session_id INT REFERENCES onboarding_sessions(id) ON DELETE SET NULL,
    profile_version INT NOT NULL DEFAULT 1,
    data JSONB NOT NULL DEFAULT '{}',
    validated_at TIMESTAMP,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uq_company_onboarding_profile_company UNIQUE (company_id)
);

COMMENT ON TABLE company_onboarding_profiles IS 'Latest merged structured profile for calibration and personalisation.';

-- ---------------------------------------------------------------------------
-- Calibration pipeline after interview + integrations (sync hooks, report flags).
-- steps: JSON checklist state, e.g. {"extract_profile":"ok","seed_products":"pending"}
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS onboarding_calibration_runs (
    id SERIAL PRIMARY KEY,
    company_id INT NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
    session_id INT REFERENCES onboarding_sessions(id) ON DELETE SET NULL,
    profile_id INT REFERENCES company_onboarding_profiles(id) ON DELETE SET NULL,
    status VARCHAR(32) NOT NULL DEFAULT 'queued',
    steps JSONB NOT NULL DEFAULT '{}',
    sync_xero_requested_at TIMESTAMP,
    sync_qbo_requested_at TIMESTAMP,
    error TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    completed_at TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_onboarding_calibration_company
    ON onboarding_calibration_runs (company_id, created_at DESC);

COMMENT ON TABLE onboarding_calibration_runs IS 'Layer-3 job record; wire to POST /api/integrations/xero|quickbooks/sync and seed endpoints.';
