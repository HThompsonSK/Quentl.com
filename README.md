# Quentl

Founder-focused forecasting and runway tooling. The FastAPI backend serves static HTML from `frontend/` at `/`, the logged-in app lives at **`/app/`**, and APIs under **`/api/*`**.

## Local development

```bash
npm run dev
```

Opens the Python server (see `package.json`); default listen port is **`9090`** unless you set **`PORT`** (see `backend/main.py`).

- Marketing site: `http://127.0.0.1:9090/`
- App dashboard: `http://127.0.0.1:9090/app/`
- **Try Quentl free** (marketing and sign-up) goes to **`/onboarding.html?return=/app/`** — MCQ company questions, optional **Xero / QuickBooks** connect (same OAuth as Settings), a **bank preference** step (direct bank feeds are not implemented yet; choices are saved to your profile), then the dashboard. **Sign in** uses **`/sign-in.html`**. Same origin as `/api/*` for both.

The onboarding page calls **`GET /api/onboarding/open-session`** and automatically falls back to **`GET /api/onboarding/sessions/latest`** if the server returns **404** (older deployments without `open-session`). It also creates a session **on load** so API problems surface before the last step.

Apply **`database/migrations/002_onboarding_sketch.sql`** to your Postgres for onboarding sessions and profile merge. **Bank account linking** (Plaid / Open Banking style) is not in this repo yet; the wizard records preference only until a provider is integrated. If you must split HTML and API origins, add `?api_base=` to the setup URL or set `window.__API_BASE__`.

## Connecting a domain

1. **DNS** — Point your apex domain (or subdomain) **A/AAAA** records to the host running the app, or **CNAME** to your cloud load balancer / tunnel hostname.
2. **HTTPS** — Terminate TLS (e.g. **Caddy**, **nginx**, or your cloud LB) and reverse-proxy to the process that runs `main.py` (e.g. **uvicorn**).
3. **Environment** — Set **`APP_BASE_URL`** to your public origin (e.g. `https://quentl.example`) so OAuth callbacks for Xero / QuickBooks match your integration apps.

Smoke checks:

- `https://your-domain/` → marketing landing  
- `https://your-domain/app/` → dashboard  
- `https://your-domain/api/...` → API routes  

Keeping marketing and the app on the **same origin** avoids extra CORS setup and keeps OAuth redirect URIs simple.

Integration logos on the marketing homepage (`frontend/assets/integrations/`) are from **Simple Icons** (MIT); replace with vendor-approved assets if your trademark guidelines require it.
