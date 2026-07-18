# job.run.place — AI Resume Tailoring

An AI system that builds a structured resume, tailors it to any job
description, scores it (match + ATS), self-refines flaws, and writes recruiter
outreach — monetized with a credit system + PRO subscription via Razorpay.
Auto-apply (LinkedIn, Greenhouse/Lever, Workday, Indeed) is planned for Phase 2.

## Pricing model (all env-configurable — change without touching code)

| | |
|---|---|
| **Credits** | 1 credit = 1 resume generation (scoring, auto-refinement, edits, template/layout changes, and regenerations are free after that) |
| **Packs** | `CREDIT_PACKS` sizes (default 3/5/10) × `CREDIT_PRICE` (default ₹49/credit) |
| **PRO** | `PRO_PRICE` (default ₹299)/month → `PRO_MONTHLY_CREDITS` (30) + premium templates 🔒 + outreach messages |
| **Signup** | `SIGNUP_CREDITS` (default 3) free credits |

Payments: **Razorpay** standard checkout — server-priced orders, HMAC-SHA256
signature verification, idempotent credit granting (`app/routers/billing.py`).

## Production hardening

- **Fail-safe by design.** Dev shortcuts (dev-login, dev credit-grant) are
  enabled *only when no real Google/Razorpay keys are configured* — so a live
  deploy can never expose them, even if `ENVIRONMENT` is misconfigured.
  `ENVIRONMENT` (default `production`) additionally controls API docs and the
  `/healthz` detail; secure cookies switch on automatically when `BASE_URL` is
  HTTPS. Startup **refuses to boot** with real keys + a default `APP_SECRET_KEY`.
- **Payments verified end-to-end**: server-priced orders, HMAC signature check,
  **and** a capture-status/amount reconciliation call to Razorpay before any
  credit is granted (idempotent).
- Credits are charged then committed *before* the LLM call (no DB lock held
  across it), with an automatic **refund** if generation fails.
- Per-user/IP **rate limiting** (LRU-bounded, tight budget on LLM endpoints;
  trusts `X-Forwarded-For` only when `TRUST_PROXY=true`), security headers,
  `robots.txt` disallow, session-auth on all data endpoints.
- **Supabase RLS enabled with no policies** on every table (run once — see
  below). The backend connects as the `postgres` superuser and bypasses RLS as
  normal, but this closes Supabase's public REST API (`/rest/v1/...`, exposed
  by every project regardless of whether you use their client SDK) to zero
  access for anyone who might obtain the project's `anon` key.

```sql
ALTER TABLE users ENABLE ROW LEVEL SECURITY;
ALTER TABLE resumes ENABLE ROW LEVEL SECURITY;
ALTER TABLE tailored_applications ENABLE ROW LEVEL SECURITY;
ALTER TABLE outreach_messages ENABLE ROW LEVEL SECURITY;
ALTER TABLE payment_orders ENABLE ROW LEVEL SECURITY;
ALTER TABLE job_applications ENABLE ROW LEVEL SECURITY;
```

> Re-run this one line for any new table added later — RLS is per-table and
> `create_all()` won't turn it on for you.

## Stack (all free)

- **Backend:** FastAPI + Uvicorn
- **Auth:** Google OAuth (Authlib) **or** email+password (bcrypt), with a local
  dev-login fallback
- **LLM:** Gemini/Gemma (Google AI) **and** Groq, with multi-key rotation and
  cross-provider failover (`app/llm_providers.py`)
- **Database:** Supabase (free Postgres) via SQLAlchemy + psycopg2.
  Falls back to SQLite (zero-config) for local dev if `DATABASE_URL` is SQLite.
- **Parsing:** `pypdf` + `python-docx` (resumes); `beautifulsoup4` + `httpx`
  (job postings fetched from a URL, with SSRF hardening)
- **Resume export:** `.docx` (`python-docx`) **and** `.pdf` (`fpdf2`), 6 templates
- **Frontend:** single static `index.html` (no build step), light/dark mode, a
  fully structured resume editor (no JSON), and an in-app live PDF preview

## How it works

1. Sign in with Google, or with **email + password** (useful when Google OAuth
   isn't set up yet — e.g. to test the Razorpay checkout flow independently),
   or Dev login locally.
2. **Upload your resumes** (PDF/DOCX/TXT) — the AI parses each into structured
   data and immediately opens an **editable, structured view of every parsed
   field** so you can review/fix it (no JSON anywhere). Upload several; everything
   merges into one **consolidated profile**. You can also **type in extra
   experience/skills** (free text) or add data manually. A one-click **ATS
   score** rates your profile's strengths/flaws.
3. **Add a job** — paste a posting **URL** (we fetch and extract the role +
   description, SSRF-safely) or type it in — then the AI **analyzes fit** and
   shows a **match score**. If your profile lacks relevant material, it asks
   targeted **gap questions** so you can supply real missing details.
4. **Pick a template** from a gallery of **live mini-previews** rendered with
   your own data, and choose whether to **include a professional summary** (the
   recommended **Technical Pro** template is a strict one-page, ATS-first layout
   that omits the summary by default).
5. The AI **generates a tailored resume** from your real content (+ your answers),
   showing the **new match & ATS scores** (before → after). It rewrites bullets
   to be job-specific and professional, emphasizing **bold quantified impact**
   where the source supports it (no number-stuffing). It's **date-aware** (won't
   flag a current role as "future-dated"). Nothing is fabricated; contact
   details and education are **locked to your real values**.
6. **Preview** the real rendered **PDF in-app**, **edit** content inline,
   **reorder sections** (move any section up/down) or **toggle the summary**,
   **switch templates** live, or **regenerate** with optional feedback — then
   **download** as **`.pdf` or `.docx`**. Layout changes update instantly and
   never call the LLM.
7. **Generate outreach** for the role — a **cold email**, a **LinkedIn
   connection note + message**, and a **referral request** — personalized to the
   company and your strongest relevant points, with clear intent and impact.
   Copy each with one click; pick a tone and add an optional hook; regenerate.
8. Every generated resume is **auto-saved to the Job Tracker** (status, notes,
   match score, re-download). Track all your applications in one place.

### True one-page check
We render the resume to PDF with `fpdf2` and read the **actual page count**.
`compute_fit` retries at progressively smaller font sizes / tighter margins and
returns the largest that genuinely fits **one page**; the `.docx` reuses the same
fit so both formats match. The result screen shows a "✓ Fits 1 page" badge (or a
warning + page count if content is too long to fit even at the smallest size, so
you can trim via edit/regenerate rather than silently dropping content).

## Setup

```bash
python -m venv .venv
.venv\Scripts\activate            # Windows
pip install -r requirements.txt
copy .env.example .env            # then fill in keys
uvicorn app.main:app --reload
```

Open http://localhost:8000

### Keys

- **Gemini/Gemma (free):** https://aistudio.google.com/apikey → set
  `GEMINI_API_KEYS` (comma-separated for several free accounts). Adjust
  `GEMINI_MODELS` if a model ID returns 404.
- **Groq (free):** https://console.groq.com/keys → `GROQ_API_KEYS`
  (comma-separated). Used as fallback when `PRIMARY_PROVIDER=gemini`.
  > Requests round-robin across **all** keys/models of both providers and fail
  > over automatically when one is rate-limited — several free keys behave like
  > one larger quota.
- **Supabase (free Postgres):** create a project → Settings → Database →
  Connection string → **Session pooler** (port 5432). Put it in `DATABASE_URL`
  (keep `?sslmode=require`). Tables are auto-created on first boot.
- **Google OAuth:** https://console.cloud.google.com/apis/credentials
  - Authorized redirect URI: `http://localhost:8000/auth/callback`
  - Set `GOOGLE_CLIENT_ID` and `GOOGLE_CLIENT_SECRET`
  - Without these, use the **Dev login** button (local only).

## API

| Method | Path | Purpose |
|---|---|---|
| GET  | `/healthz` | Config/health + LLM pool status |
| GET  | `/auth/login` · `/auth/callback` · `/auth/me` · `POST /auth/logout` | Google OAuth |
| POST | `/auth/signup` · `/auth/login` | Email+password account creation / login (bcrypt) |
| POST | `/auth/dev-login` | Local fake login (when no Google/Razorpay keys set) |
| GET  | `/resumes/templates` | List the 6 templates (with style) + empty skeleton |
| POST | `/resumes/upload` | Upload PDF/DOCX/TXT → AI-parsed structured resume |
| POST | `/resumes/freeform` | Add free-text experience/skills → parsed + merged |
| GET  | `/resumes/profile` | Consolidated profile merged across all resumes |
| GET/POST | `/resumes` | List / create resumes |
| GET/PUT/DELETE | `/resumes/{id}` | Manage a resume |
| GET  | `/resumes/{id}/download` | Download resume `.docx` |
| POST | `/score/ats` | ATS score + strengths/flaws for a resume or the profile |
| POST | `/score/match` | Match score of a resume/profile vs a job description |
| POST | `/tailor/extract-url` | Fetch a job posting URL → title/company/description |
| POST | `/tailor/analyze` | Assess fit, gap questions, "before" match score |
| POST | `/tailor/generate` | Generate tailored resume (+ ATS & match scores), auto-tracked |
| PUT  | `/tailor/{id}` | Save inline edits, then re-score |
| PUT  | `/tailor/{id}/template` | Switch the visual template |
| PUT  | `/tailor/{id}/layout` | Reorder sections + toggle the professional summary |
| POST | `/tailor/{id}/regenerate` | Regenerate with optional feedback |
| POST/GET | `/tailor/{id}/outreach` | Generate / fetch cold-email, LinkedIn & referral messages |
| GET  | `/tailor` | List past tailored applications |
| GET  | `/tailor/{id}/download?format=pdf\|docx` | Download tailored resume |
| GET  | `/tailor/{id}/preview.pdf` | Inline PDF for the in-app preview |
| GET/POST | `/applications` | Job tracker: list / create entries |
| PUT/DELETE | `/applications/{id}` | Update status/notes / delete a tracker entry |
| GET  | `/billing/info` | Credits, plan, and env-priced catalog |
| POST | `/billing/order` | Create a Razorpay order (server-priced) |
| POST | `/billing/verify` | Verify payment signature → grant credits/PRO |

## Database migration (existing Supabase projects)

`create_all()` only creates **new** tables — run this once in the Supabase SQL
editor when upgrading to v8 (idempotent):

```sql
ALTER TABLE users ADD COLUMN IF NOT EXISTS credits INTEGER NOT NULL DEFAULT 3;
ALTER TABLE users ADD COLUMN IF NOT EXISTS plan VARCHAR DEFAULT 'free';
ALTER TABLE users ADD COLUMN IF NOT EXISTS pro_expires_at TIMESTAMP;
-- payment_orders is a new table: created automatically on next boot.

-- Email+password auth: google_sub must become nullable, plus a password_hash column.
ALTER TABLE users ALTER COLUMN google_sub DROP NOT NULL;
ALTER TABLE users ADD COLUMN IF NOT EXISTS password_hash VARCHAR;
```

## Deploy for free (Render + Supabase)

Everything is config-driven via env vars, so no code changes are needed.

1. **Push to GitHub.** `.env` and `*.db` are gitignored — secrets stay out of git.
2. **Database:** use your free **Supabase** project's *Session pooler* URI as
   `DATABASE_URL` (keep `?sslmode=require`). Tables auto-create on first boot.
3. **Render:** New → **Blueprint** → pick the repo. `render.yaml` provisions a
   free web service (`APP_SECRET_KEY` is auto-generated). In the service's
   **Environment** tab, fill the `sync:false` vars:
   `BASE_URL` (your `https://<app>.onrender.com`), `DATABASE_URL`,
   `GEMINI_API_KEYS`, `GROQ_API_KEYS` (optional), `GOOGLE_CLIENT_ID/SECRET`.
4. **Google OAuth:** in Google Cloud Console add the authorized redirect URI
   `https://<app>.onrender.com/auth/callback` (must match `BASE_URL`).
5. Visit your URL. Check `/healthz` to confirm keys/DB loaded.

> A `Dockerfile` is also included, so the same app deploys on Fly.io, Koyeb, or
> Hugging Face Spaces. The start command honors `$PORT`.

**Free-tier caveats:** Render free web services sleep after ~15 min idle
(first request cold-starts in ~30–60s). Supabase free projects pause after ~1
week of inactivity (first request wakes them). Both are fine for personal use.

## Roadmap (Phase 2 — auto-apply)

- Playwright adapters per platform (LinkedIn, Greenhouse/Lever, Workday, Indeed)
- Application tracker to avoid duplicates
- Cover-letter generation agent

> **Note on auto-apply:** many job sites prohibit automated submission in their
> ToS and actively block bots. Phase 2 should be built with rate limiting,
> human-in-the-loop review before submit, and respect for each site's terms.
