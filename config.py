"""
config.py — Central configuration.

Loads .env file and exposes all settings as module-level constants.
"""

import os
from dotenv import load_dotenv

load_dotenv()

# ── OpenRouter ──────────────────────────────────────────────
OPENROUTER_API_KEY: str = os.getenv("OPENROUTER_API_KEY", "")
OPENROUTER_MODEL: str = os.getenv("OPENROUTER_MODEL", "anthropic/claude-sonnet-4")
OPENROUTER_MAX_RETRIES: int = int(os.getenv("OPENROUTER_MAX_RETRIES", "3"))
OPENROUTER_BASE_URL: str = "https://openrouter.ai/api/v1"

# ── Apify ───────────────────────────────────────────────────
APIFY_API_TOKEN: str = os.getenv("APIFY_API_TOKEN", "")
APIFY_BASE_URL: str = "https://api.apify.com/v2"

# ── SMTP (Sending) ─────────────────────────────────────────
SMTP_HOST: str = os.getenv("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT: int = int(os.getenv("SMTP_PORT", "587"))
SMTP_USERNAME: str = os.getenv("SMTP_USERNAME", "")
SMTP_PASSWORD: str = os.getenv("SMTP_PASSWORD", "")
SMTP_FROM_NAME: str = os.getenv("SMTP_FROM_NAME", "")
SMTP_FROM_EMAIL: str = os.getenv("SMTP_FROM_EMAIL", "")
SMTP_USE_TLS: bool = os.getenv("SMTP_USE_TLS", "true").lower() == "true"

# ── IMAP (Reading) ─────────────────────────────────────────
IMAP_HOST: str = os.getenv("IMAP_HOST", "imap.gmail.com")
IMAP_PORT: int = int(os.getenv("IMAP_PORT", "993"))
IMAP_USERNAME: str = os.getenv("IMAP_USERNAME", "")
IMAP_PASSWORD: str = os.getenv("IMAP_PASSWORD", "")

# ── Database ───────────────────────────────────────────────
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
raw_db_url = os.getenv("DATABASE_URL", "sqlite:///sales_machine.db")
if raw_db_url.startswith("sqlite:///") and not os.path.isabs(raw_db_url.replace("sqlite:///", "")):
    db_filename = raw_db_url.replace("sqlite:///", "")
    DATABASE_URL = f"sqlite:///{os.path.join(BASE_DIR, db_filename).replace(os.sep, '/')}"
else:
    DATABASE_URL = raw_db_url

# ── ICP Scoring ────────────────────────────────────────────
ICP_SCORE_THRESHOLD: int = int(os.getenv("ICP_SCORE_THRESHOLD", "40"))

# Scoring weights (rule → points)
ICP_SCORING_RULES: dict[str, int] = {
    "hiring_engineers": 30,
    "tech_ai": 20,
    "healthtech": 20,
    "fintech": 15,
    "usa": 10,
    "europe": 5,
    "employee_20_200": 20,
    "employee_200_1000": 10,
    "product_launch": 15,
    "recent_funding": 25,
    "company_news": 15,
}

# ── Email Sending ──────────────────────────────────────────
EMAIL_SEND_DELAY_SECONDS: int = int(os.getenv("EMAIL_SEND_DELAY_SECONDS", "30"))
FOLLOWUP_1_DELAY_DAYS: int = int(os.getenv("FOLLOWUP_1_DELAY_DAYS", "3"))
FOLLOWUP_2_DELAY_DAYS: int = int(os.getenv("FOLLOWUP_2_DELAY_DAYS", "7"))

# ── Logging ────────────────────────────────────────────────
LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO")
LOG_FILE: str = os.getenv("LOG_FILE", "logs/pipeline.log")
