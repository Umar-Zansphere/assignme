# AssignMe: Autonomous Outbound Sales Machine

## Overview

This is a **fully autonomous B2B cold outreach pipeline** built in Python. It monitors business signals, discovers qualified companies, finds decision-makers, researches them, writes personalized emails, sends them, and tracks replies — all without human intervention.

> **Single run command:** `python scheduler.py --once`
> **Continuous mode:** `python scheduler.py`
> **Dashboard:** `streamlit run dashboard.py`

---

## 🛠️ Setup Guide

### Prerequisites

| Requirement | Version | Notes |
| :---------- | :-----: | :---- |
| Python | ≥ 3.11 | Uses `match`, type hints, `tomllib` |
| pip | latest | `python -m pip install --upgrade pip` |
| Git | any | For cloning the repo |
| Gmail account (or any SMTP) | — | For sending emails |
| OpenRouter account | — | Free tier works |
| Apify account | — | Free tier sufficient for most scraping |

---

### Step 1 — Clone the Repository

```powershell
git clone <your-repo-url>
cd assignme
```

---

### Step 2 — Create a Virtual Environment

```powershell
# Create venv
python -m venv .venv

# Activate it (PowerShell)
.venv\Scripts\Activate.ps1

# Activate it (CMD)
.venv\Scripts\activate.bat
```

> **Tip:** You'll see `(.venv)` prefix in your terminal when the venv is active. Always activate before running any pipeline scripts.

---

### Step 3 — Install Dependencies

```powershell
pip install -r requirements.txt
```

**What gets installed:**

| Package | Purpose |
| :------ | :------ |
| `sqlalchemy` | ORM for all database operations |
| `python-dotenv` | Loads `.env` variables into the process |
| `httpx` | Async-capable HTTP client for web scraping |
| `apscheduler` | Cron-like scheduling for `scheduler.py` |
| `streamlit` | Web dashboard UI |
| `dnspython` | MX record lookups for email verification |
| `pandas` | Data tables in the dashboard |
| `jinja2` | Template rendering |

---

### Step 4 — Obtain API Credentials

#### 4a. OpenRouter (LLM API)
1. Go to [https://openrouter.ai](https://openrouter.ai) and create a free account.
2. Navigate to **Keys** → **Create new key**.
3. Copy the key starting with `sk-or-v1-...`
4. The free model is `openai/gpt-oss-20b:free` — no credit card needed.

#### 4b. Apify (Web Scraping)
1. Go to [https://apify.com](https://apify.com) and create a free account.
2. Navigate to **Settings** → **Integrations** → **API tokens**.
3. Copy the token starting with `apify_api_...`
4. Free tier gives $5/month of compute — enough for dozens of companies.

#### 4c. Gmail App Password (SMTP + IMAP)

Gmail requires an **App Password** (not your regular password) for programmatic access.

1. Go to your Google Account → **Security**.
2. Enable **2-Step Verification** (required for App Passwords).
3. Go to **Security** → **App passwords** → Select app: **Mail** → Select device: **Windows Computer**.
4. Click **Generate** — copy the 16-character password (e.g., `klug ppqm iarr vaca`).
5. Use this as both `SMTP_PASSWORD` and `IMAP_PASSWORD`.

> **Note:** If you use a custom domain (e.g., Google Workspace), the process is the same but done in the Admin Console.

---

### Step 5 — Configure Environment Variables

```powershell
# Copy the example env file
copy .env.example .env
```

Now open `.env` and fill in your values:

```env
# ============================================================
# AI Sales Machine — Environment Configuration
# ============================================================

# --- OpenRouter (LLM) ---
OPENROUTER_API_KEY=sk-or-v1-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
OPENROUTER_MODEL=openai/gpt-oss-20b:free    # Free model, no billing needed
OPENROUTER_MAX_RETRIES=3

# --- Apify (Scraping) ---
APIFY_API_TOKEN=apify_api_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx

# --- Email Sending (SMTP) ---
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587                               # Use 465 for SSL, 587 for TLS
SMTP_USERNAME=you@gmail.com
SMTP_PASSWORD=xxxx xxxx xxxx xxxx          # Gmail App Password (16 chars)
SMTP_FROM_NAME=Your Name
SMTP_FROM_EMAIL=you@gmail.com
SMTP_USE_TLS=true                           # true for port 587, false for 465

# --- Email Reading (IMAP) ---
IMAP_HOST=imap.gmail.com
IMAP_PORT=993
IMAP_USERNAME=you@gmail.com
IMAP_PASSWORD=xxxx xxxx xxxx xxxx          # Same Gmail App Password

# --- Database ---
DATABASE_URL=sqlite:///sales_machine.db    # SQLite file in project root

# --- ICP Scoring ---
ICP_SCORE_THRESHOLD=40                     # Companies scoring ≥40 get qualified

# --- Email Sequence Timing ---
EMAIL_SEND_DELAY_SECONDS=30               # Wait 30s between sends
FOLLOWUP_1_DELAY_DAYS=3                   # First follow-up: 3 days after initial
FOLLOWUP_2_DELAY_DAYS=7                   # Second follow-up: 7 days after initial

# --- Logging ---
LOG_LEVEL=INFO
LOG_FILE=logs/pipeline.log
```

> ⚠️ **Never commit `.env` to Git.** It's already in `.gitignore`.

---

### Step 6 — Initialize the Database

```powershell
python database.py
```

Expected output:
```
[OK] Database initialized successfully.
```

This creates `sales_machine.db` with all tables. Safe to run multiple times (uses `CREATE TABLE IF NOT EXISTS`).

---

### Step 7 — Verify Your Setup

Run a quick sanity check on each critical dependency:

```powershell
# Check Python version
python --version

# Check all imports load correctly
python -c "from database import get_session; from models import Company; print('DB OK')"
python -c "from openrouter_client import call_llm; print('OpenRouter OK')"
python -c "from config import SMTP_USERNAME; print('SMTP config:', SMTP_USERNAME)"

# Check database has tables
python -c "import sqlite3; c=sqlite3.connect('sales_machine.db'); print(c.execute(\"SELECT name FROM sqlite_master WHERE type='table'\").fetchall())"
```

---

## 🚀 Running the Pipeline

### Option A — Full Automatic Mode (Recommended for Production)

```powershell
# Run all stages continuously on scheduled intervals
python scheduler.py
```

This starts APScheduler and runs each module on its configured interval:
- `watcher` every 6h, `enrichment` every 2h, `scorer` every 2h
- `finder`, `verifier`, `research`, `email_writer` every 4h
- `sender` every 1h, `reply_checker` every 30min

Press `Ctrl+C` to stop gracefully.

---

### Option B — Single Full Pipeline Pass (Good for Testing)

```powershell
# Run all stages once, in order, and exit
python scheduler.py --once
```

This runs: `watcher → enrichment → scorer → finder → verifier → research → email_writer → sender → reply_checker`

---

### Option C — Run Individual Stages Manually

Useful for debugging a specific stage or re-running a failed step:

```powershell
# Stage 1: Detect new business signals and add companies
python watcher.py

# Stage 2: Enrich company profiles (industry, country, size)
python enrichment.py

# Stage 3: Score companies against ICP criteria
python scorer.py

# Stage 4: Find decision-maker contacts
python finder.py

# Stage 5: Verify discovered email addresses
python verifier.py

# Stage 6: Generate deep research profiles
python research.py

# Stage 7: Write personalized 3-email sequences
python email_writer.py

# Stage 8: Send scheduled emails (DRY RUN - safe, won't actually send)
python sender.py --dry-run

# Stage 8: Send scheduled emails (LIVE - actually sends emails!)
python sender.py

# Stage 9: Check inbox for replies (DRY RUN)
python reply_checker.py --dry-run

# Stage 9: Check inbox for replies (LIVE)
python reply_checker.py
```

---

### Launch the Monitoring Dashboard

```powershell
streamlit run dashboard.py
```

Opens at **http://localhost:8501** in your browser. Shows live metrics, pipeline funnel, email log, and reply log. Auto-refreshes every 30 seconds.

---

## 🔍 Monitoring & Logs

### Real-time log tail

```powershell
# Watch live pipeline logs
Get-Content logs\pipeline.log -Wait -Tail 50
```

### Quick database status check

```powershell
python -c "
import sys
from database import get_session
from models import Company, Email

with get_session() as s:
    counts = {}
    for c in s.query(Company).all():
        counts[c.status] = counts.get(c.status, 0) + 1
    emails = s.query(Email).count()
    scheduled = s.query(Email).filter_by(status='SCHEDULED').count()
    sent = s.query(Email).filter_by(status='SENT').count()

print('--- Company Status Breakdown ---')
for status, count in sorted(counts.items()):
    print(f'  {status:<20} {count}')
print(f'\n--- Emails ---')
print(f'  Total:     {emails}')
print(f'  Scheduled: {scheduled}')
print(f'  Sent:      {sent}')
"
```

---

## ⚡ Quick Command Reference

```powershell
# ── Setup ─────────────────────────────────────────────────
python -m venv .venv && .venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy .env.example .env                  # then edit .env
python database.py                      # init DB tables

# ── Run ───────────────────────────────────────────────────
python scheduler.py --once             # single full pass
python scheduler.py                    # continuous mode
streamlit run dashboard.py             # monitoring UI

# ── Manual Stages ─────────────────────────────────────────
python watcher.py                      # detect signals
python enrichment.py                   # enrich companies
python scorer.py                       # score companies
python finder.py                       # find contacts
python verifier.py                     # verify emails
python research.py                     # generate research
python email_writer.py                 # write emails
python sender.py --dry-run             # preview sends
python sender.py                       # send emails
python reply_checker.py --dry-run      # preview reply check
python reply_checker.py                # check replies

# ── Diagnostics ───────────────────────────────────────────
Get-Content logs\pipeline.log -Wait -Tail 50   # live logs
python database.py                             # re-init DB
```

---

## Architecture: The Data Flow

```
[External World]
      │
      ▼
Stage 1: watcher.py       ──→  Companies added (status=NEW_SIGNAL)
      │
      ▼
Stage 2: enrichment.py    ──→  Industry, country, size filled (status=ENRICHED)
      │
      ▼
Stage 3: scorer.py        ──→  ICP score assigned → QUALIFIED (≥40) or REJECTED
      │
      ▼
Stage 4: finder.py        ──→  Decision-maker email discovered (status=CONTACT_FOUND)
      │
      ▼
Stage 5: verifier.py      ──→  Email verified via DNS (status=EMAIL_VERIFIED)
      │
      ▼
Stage 6: research.py      ──→  Deep research profile generated (status=RESEARCH_DONE)
      │
      ▼
Stage 7: email_writer.py  ──→  3-email sequence drafted (status=EMAIL_READY)
      │
      ▼
Stage 8: sender.py        ──→  Emails dispatched via SMTP (status=EMAIL_SENT)
      │
      ▼
Stage 9: reply_checker.py ──→  Inbox checked for replies (status=REPLIED)
```

All data lives in a single **SQLite database** (`sales_machine.db`) managed by SQLAlchemy ORM.

---

## Database Tables (models.py)

| Table | Purpose |
| :---- | :------ |
| `companies` | Central entity. Every tracked company + its pipeline `status` |
| `signals` | Business events that triggered a company's entry (job posts, news, funding) |
| `contacts` | Decision-makers discovered for each company |
| `research` | AI-generated research profile (pain points, tech stack, news) |
| `emails` | Email sequence rows (initial + 2 follow-ups) per company |
| `campaigns` | Groups emails into a named outreach campaign |
| `reply_logs` | Recorded inbound reply events matched to sent emails |
| `settings` | Key-value config store for runtime settings |

### Company Status Flow
```
NEW_SIGNAL → ENRICHED → QUALIFIED ──→ CONTACT_FOUND → EMAIL_VERIFIED
                       └─→ REJECTED   → RESEARCH_DONE → EMAIL_READY
                                       → EMAIL_SENT → REPLIED
```

---

## Shared Infrastructure

### `database.py` — Session Management

```python
with get_session() as session:
    # All DB work here; auto-commits on success, rolls back on exception
```

- Creates the SQLAlchemy engine pointing to `DATABASE_URL` from config.
- Enables **SQLite WAL mode**, `busy_timeout=30000ms`, and `foreign_keys=ON` for safe concurrent access.
- `get_session()` is a context manager: commits on exit, rolls back on exception, always closes.

### `config.py` — Central Configuration

All environment variables from `.env` are exposed as constants here:

| Variable | Purpose |
| :------- | :------ |
| `OPENROUTER_API_KEY` / `OPENROUTER_MODEL` | LLM access |
| `APIFY_API_TOKEN` | Web scraping actor API |
| `SMTP_HOST/PORT/USERNAME/PASSWORD` | Email sending credentials |
| `IMAP_HOST/PORT/USERNAME/PASSWORD` | Inbox reading credentials |
| `DATABASE_URL` | SQLite file path (resolved to absolute) |
| `ICP_SCORE_THRESHOLD` | Minimum score to qualify a company (default: 40) |
| `ICP_SCORING_RULES` | Dict of scoring rule → point value |
| `FOLLOWUP_1/2_DELAY_DAYS` | Days between email sequence steps |

### `openrouter_client.py` — LLM Gateway

- All AI calls route through **OpenRouter** (multi-model API proxy).
- `call_llm(system_prompt, user_prompt)` → plain text response.
- `call_llm_with_schema(system_prompt, user_prompt, required_keys)` → validated JSON dict.
- Uses **exponential backoff with 5 retries** (5s, 10s, 20s, 40s, 80s) to handle free-tier `429 Too Many Requests` errors.
- Default model: `openai/gpt-oss-20b:free` (configurable via `OPENROUTER_MODEL`).

### `apify_client.py` — Web Scraping Toolkit

Provides functions for acquiring external data:

| Function | Purpose |
| :------- | :------ |
| `scrape_website(url)` | Returns text content of a webpage (direct `httpx` GET, no Apify) |
| `search_google(query, max)` | Google search results via Apify SERP actor |
| `get_linkedin_person(url)` | LinkedIn profile scraping |
| `get_linkedin_company(url)` | LinkedIn company scraping |
| `get_company_news(company_name)` | Recent news articles for a company |
| `find_decision_makers(company_name, website)` | Discovers CTO/VP/Director contacts |

### `utils.py` — Shared Utilities

- `get_logger(name)` — Creates a logger writing to both console and `logs/pipeline.log`.
- `utcnow()` — Returns UTC-aware datetime.
- `@retry(max_attempts, delay)` — Decorator for automatic function retries.

---

## Stage-by-Stage Script Reference

---

### Stage 1: `watcher.py` — Signal Detection

**What it does:** Monitors external sources for business events (signals) and creates `Company` records in the database.

**Input:** External RSS feeds, news APIs, Product Hunt, GitHub trending.

**Output:** New `Company` rows with `status=NEW_SIGNAL` + `Signal` rows for each detected event.

**How it works:**
1. Polls configured signal sources (news feeds, job boards, tech news).
2. For each signal, extracts the company name and event type (`JOB_POSTING`, `NEWS`, `PRODUCT_LAUNCH`, `FUNDING`).
3. Checks if the company already exists in the database using a `UNIQUE` constraint on `companies.name`.
4. If new: creates a `Company` row + a `Signal` row linked to it.
5. If existing: adds a new `Signal` row and refreshes the `updated_at` timestamp.

**Signal Types:**
- `JOB_POSTING` — Company is actively hiring engineers (strong buying intent signal)
- `NEWS` — Company mentioned in tech/business news
- `PRODUCT_LAUNCH` — Company launched a product (growth signal)
- `FUNDING` — Company received investment (budget signal)

**Key config:** Signal sources are hardcoded in the script's source list.

---

### Stage 2: `enrichment.py` — Company Enrichment

**What it does:** Populates missing company profile fields (industry, country, employee count, website) using web scraping and LLM inference.

**Input:** Companies with `status=NEW_SIGNAL` and no industry/country data.

**Output:** Companies updated with profile data, `status=ENRICHED`.

**How it works:**
1. Queries all companies where `status="NEW_SIGNAL"` and `industry IS NULL`.
2. For each company, tries to:
   - **Direct website scrape** — fetches the company's website and extracts text.
   - **LLM inference** — sends the text + company name to the LLM with a structured prompt asking for `{industry, country, employee_count, description}`.
3. If LLM returns valid JSON with the fields, updates the `Company` row.
4. Commits the enrichment and moves to `ENRICHED` status.

**LLM Prompt structure:** Returns a JSON object with keys: `industry`, `country`, `employee_count`, `description`.

---

### Stage 3: `scorer.py` — ICP (Ideal Customer Profile) Scoring

**What it does:** Assigns a numeric score to each enriched company based on how well it fits the target customer profile.

**Input:** Companies with `status=ENRICHED`.

**Output:** Companies scored; `QUALIFIED` (score ≥ threshold) or `REJECTED` (below threshold).

**How it works:**
1. Queries all companies with `status=ENRICHED`.
2. For each company, evaluates a set of weighted rules against the company's data:

| Rule | Points | Condition |
| :--- | :----: | :-------- |
| `hiring_engineers` | +30 | Has a `JOB_POSTING` signal |
| `tech_ai` | +20 | Industry is Tech/Software/AI/SaaS |
| `healthtech` | +20 | Industry is Health/Bio/Med |
| `fintech` | +15 | Industry is Finance/Fintech/Banking |
| `usa` | +10 | Country is United States |
| `europe` | +5 | Country is UK, Germany, France, etc. |
| `employee_20_200` | +20 | 20–200 employees (sweet spot) |
| `employee_200_1000` | +10 | 200–1000 employees |
| `product_launch` | +15 | Has a `PRODUCT_LAUNCH` signal |
| `recent_funding` | +25 | Has a `FUNDING` signal |
| `company_news` | +15 | Has a `NEWS` signal |

3. Sums the score and compares to `ICP_SCORE_THRESHOLD` (default: **40**).
4. Sets `company.icp_score` and `company.status = "QUALIFIED"` or `"REJECTED"`.

---

### Stage 4: `finder.py` — Decision Maker Finder

**What it does:** Discovers the right contact at each qualified company — typically a CTO, VP of Engineering, or Head of QA.

**Input:** Companies with `status=QUALIFIED`.

**Output:** `Contact` rows created, company `status=CONTACT_FOUND`.

**How it works:**
1. Queries all `QUALIFIED` companies.
2. For each company, calls `apify_client.find_decision_makers(company_name, website)`.
3. The function tries multiple strategies:
   - **LinkedIn search** for the company + engineering leadership titles.
   - **Website team/about page** scraping.
   - **LLM inference** to guess email patterns (e.g., `firstname@domain.com`).
4. Creates a `Contact` row with `name`, `email`, `role`, `linkedin_url`.
5. Sets company `status=CONTACT_FOUND`.

**Typical target roles:** CTO, VP Engineering, Head of QA, Director of Software Engineering, Engineering Manager.

---

### Stage 5: `verifier.py` — Email Verification

**What it does:** Validates that the discovered email address is real and deliverable before investing in research and email writing.

**Input:** Contacts with `verified=NULL` or `verified="PENDING"` for `CONTACT_FOUND` companies.

**Output:** Contact `verified="VALID"` or `"INVALID"`, company `status=EMAIL_VERIFIED`.

**How it works:**
1. Queries contacts needing verification.
2. Runs a 3-step verification pipeline:
   - **Syntax check** — validates email format with regex.
   - **Domain MX record lookup** — confirms the domain has a mail server using `dnspython`.
   - **Corporate domain check** — rejects free email providers (gmail, yahoo, etc.).
3. Sets `contact.verified = "VALID"` or `"INVALID"`.
4. If at least one valid contact exists: sets company `status=EMAIL_VERIFIED`.

---

### Stage 6: `research.py` — Deep Research

**What it does:** Generates a rich, AI-synthesized research profile for each company to power hyper-personalized emails.

**Input:** Companies with `status=EMAIL_VERIFIED`.

**Output:** `Research` row created with summary, pain points, tech stack, and recent news. Company `status=RESEARCH_DONE`.

**How it works:**
1. Queries all `EMAIL_VERIFIED` companies.
2. For each company, gathers raw context:
   - Scrapes the company's website for product/about content.
   - Fetches recent news articles via `get_company_news()`.
   - Loads any previously stored signal descriptions.
3. Sends the raw context to the LLM with a structured research prompt.
4. LLM returns JSON with:
   - `summary` — 2–3 sentence company overview.
   - `pain_points` — JSON array of engineering/QA/testing challenges.
   - `tech_stack` — JSON array of inferred tools/languages.
   - `recent_news` — Single most relevant recent event string.
5. Stores everything in a `Research` row.
6. Sets company `status=RESEARCH_DONE`.

> ⚠️ **Important:** The LLM call happens **outside** the DB transaction — session opens only to read inputs, closes, LLM runs, then a new session opens just to write the result. This prevents long locks.

---

### Stage 7: `email_writer.py` — Email Sequence Generator

**What it does:** Generates a 3-step personalized outreach email sequence using the research profile.

**Input:** Companies with `status=RESEARCH_DONE` + a valid `Contact` + a `Research` profile.

**Output:** 3 `Email` rows per company (initial + 2 follow-ups), company `status=EMAIL_READY`.

**How it works:**
1. Queries all `RESEARCH_DONE` companies.
2. For each company:
   - **Step 1 (Read session):** Loads company fields, contact details, research data into local variables, then closes the session.
   - **Step 2 (Outside any session):** Builds a rich prompt with all context and calls the LLM to generate a 3-email JSON sequence.
   - **Step 3 (Write session):** Opens a new fast session, inserts the 3 `Email` rows, updates company status to `EMAIL_READY`, commits and closes.

**Email Sequence Structure:**

| Sequence | Timing | Purpose |
| :------- | :----- | :------ |
| `sequence_number=0` | Immediately | Initial cold pitch, references a specific signal |
| `sequence_number=1` | +3 days | Value-add follow-up, re-engages with new angle |
| `sequence_number=2` | +7 days | Final "last chance" bump, low-pressure close |

All emails start as `status=SCHEDULED`.

---

### Stage 8: `sender.py` — Email Dispatcher

**What it does:** Sends all `SCHEDULED` emails that are due, via SMTP.

**Input:** `Email` rows with `status=SCHEDULED` and `scheduled_at ≤ NOW`.

**Output:** Emails dispatched; `status=SENT`, `sent_at` timestamp + `message_id` stored. Company `status=EMAIL_SENT`.

**How it works:**
1. Queries all emails where `status=SCHEDULED` and `scheduled_at <= utcnow()`.
2. For each email, reads the associated `Contact` (recipient) and `Company`.
3. Constructs a MIME email with proper `From:`, `To:`, `Subject:`, and `Message-ID:` headers.
4. Connects to SMTP server using TLS (`SMTP_HOST`, `SMTP_PORT`, credentials from config).
5. Sends the email; stores the `Message-ID` header value in `email.message_id` for reply tracking.
6. Updates `email.status = "SENT"` and `email.sent_at = now()`.
7. If first email in sequence for a company, updates `company.status = "EMAIL_SENT"`.
8. Respects `EMAIL_SEND_DELAY_SECONDS` between sends to avoid spam triggers.

**Dry run:** `python sender.py --dry-run` logs what it would send without actually sending.

---

### Stage 9: `reply_checker.py` — Reply Detection

**What it does:** Connects to your inbox via IMAP and matches inbound replies to sent emails.

**Input:** IMAP inbox + `Email` rows with `status=SENT` and a stored `message_id`.

**Output:** `ReplyLog` rows created. Pending follow-ups `CANCELLED`. Company `status=REPLIED`.

**How it works:**
1. Queries all sent emails and builds a set of known `Message-ID` values.
2. Connects to the IMAP server and searches for recent emails (last 7 days).
3. For each inbox email, reads the `In-Reply-To` and `References` headers.
4. If any header value matches a known `Message-ID`, it's a reply.
5. Creates a `ReplyLog` row with the reply subject, body (truncated to 2000 chars), and sender.
6. Sets company `status = "REPLIED"`.
7. **Cancels all remaining `SCHEDULED` emails** for that company (no more follow-ups needed).
8. Skips replies already logged (idempotent via `reply_from` dedup check).

---

### Stage 10: `scheduler.py` — Orchestrator

**What it does:** Runs all pipeline stages automatically on a configurable schedule, each as an isolated subprocess.

**How it works:**
- Uses **APScheduler** (`BlockingScheduler`) to trigger each module on an interval.
- Each module runs as `subprocess.run(["python", "module.py"])` — fully isolated, crash in one stage doesn't affect others.
- Output from each subprocess is captured and logged.

**Default schedule:**

| Module | Interval | Reason |
| :----- | :------: | :----- |
| `watcher` | Every 6h | New signals don't need frequent polling |
| `enrichment` | Every 2h | Process new signals quickly |
| `scorer` | Every 2h | Score right after enrichment |
| `finder` | Every 4h | Contact discovery can be slower |
| `verifier` | Every 4h | Follows finder |
| `research` | Every 4h | LLM-heavy, rate-limited |
| `email_writer` | Every 4h | LLM-heavy, rate-limited |
| `sender` | Every 1h | Send promptly when emails are due |
| `reply_checker` | Every 30m | Check for replies frequently |

**One-shot mode:** `python scheduler.py --once` runs all stages once in order and exits.

---

### `dashboard.py` — Streamlit Monitoring Dashboard

**What it does:** Provides a real-time web UI to monitor pipeline health and email performance.

**Run:** `streamlit run dashboard.py` → opens at `http://localhost:8501`

**Panels:**
- **KPI metrics:** Total signals, companies, qualified, contacts, emails sent, scheduled, replies, reply rate.
- **Pipeline funnel:** Bar chart showing companies at each status stage.
- **Companies table:** Filterable by status with score, industry, country.
- **Email log:** All sent/scheduled emails with subject, recipient, scheduled/sent timestamps.
- **Reply log:** All detected replies with from, subject, body excerpt.

Data refreshes automatically every 30 seconds via `@st.cache_data(ttl=30)`.

---

## Environment Setup (`.env`)

```env
# LLM
OPENROUTER_API_KEY=sk-or-...
OPENROUTER_MODEL=openai/gpt-oss-20b:free

# Scraping
APIFY_API_TOKEN=apify_api_...

# Email Sending (SMTP)
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USERNAME=you@yourdomain.com
SMTP_PASSWORD=your_app_password
SMTP_FROM_NAME=Your Name
SMTP_FROM_EMAIL=you@yourdomain.com

# Email Reading (IMAP, for reply detection)
IMAP_HOST=imap.gmail.com
IMAP_PORT=993
IMAP_USERNAME=you@yourdomain.com
IMAP_PASSWORD=your_app_password

# Tuning
ICP_SCORE_THRESHOLD=40
FOLLOWUP_1_DELAY_DAYS=3
FOLLOWUP_2_DELAY_DAYS=7
EMAIL_SEND_DELAY_SECONDS=30
```

---

## Common Debugging Patterns

| Symptom | Check |
| :------ | :---- |
| Companies stuck at `NEW_SIGNAL` | Run `python enrichment.py` manually; check logs for LLM errors |
| Companies stuck at `ENRICHED` | Run `python scorer.py`; check `icp_score` in DB |
| Contacts not found | Check `finder.py` logs; Apify token may be invalid or rate-limited |
| Emails stuck at `SCHEDULED` | Run `python sender.py`; check SMTP credentials in `.env` |
| No reply detection | Ensure `IMAP_*` credentials are set; check that emails have `message_id` stored |
| `429 Too Many Requests` | OpenRouter free-tier limit hit; wait a few minutes and re-run |
| Database locked error | Ensure no long-running scripts hold open sessions; WAL mode should handle concurrent reads |
