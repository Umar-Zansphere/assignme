"""
config.py — Central configuration.

Loads .env file and exposes all settings as module-level constants.
"""

import os
from dotenv import load_dotenv


# Force-clear stale LLM env vars before re-reading .env.
# Without this, Streamlit's process-level os.environ retains values
# from a previous load_dotenv even after the user comments them out.
for _key in ("LLM_PROVIDER", "LLM_BASE_URL", "LLM_MODEL", "LLM_API_KEY",
             "LLM_TEMPERATURE", "LLM_TIMEOUT", "LLM_FAST_MODEL"):
    os.environ.pop(_key, None)

load_dotenv(override=True)

# ── LLM Provider Configuration ─────────────────────────────
# Set LLM_PROVIDER to switch between backends. Everything else auto-configures.
#
# Supported providers:
#   ollama      — Local Ollama instance (free, no API key needed)
#   groq        — Groq Cloud (fast inference, free tier available)
#   openrouter  — OpenRouter (access to 100+ models)
#   openai      — OpenAI API (GPT-4o, GPT-4o-mini, etc.)
#   deepseek    — DeepSeek API (cheap, good for JSON tasks)
#   custom      — Any OpenAI-compatible endpoint (set LLM_BASE_URL manually)
#
# Provider presets (base_url / default_model):
_LLM_PRESETS: dict[str, dict] = {
    "ollama":     {"base_url": "http://localhost:11434/v1",                              "model": "qwen3:14b"},
    "groq":       {"base_url": "https://api.groq.com/openai/v1",                        "model": "qwen/qwen3.6-27b"},
    "openrouter": {"base_url": "https://openrouter.ai/api/v1",                          "model": "meta-llama/llama-3.3-70b-instruct"},
    "openai":     {"base_url": "https://api.openai.com/v1",                             "model": "gpt-4o-mini"},
    "gemini":     {"base_url": "https://generativelanguage.googleapis.com/v1beta/openai", "model": "gemini-3.5-flash"},
    "deepseek":   {"base_url": "https://api.deepseek.com/v1",                           "model": "deepseek-chat"},
    "custom":     {"base_url": "",                                                       "model": ""},
}

LLM_PROVIDER: str = os.getenv("LLM_PROVIDER", "ollama").lower()
_preset = _LLM_PRESETS.get(LLM_PROVIDER, _LLM_PRESETS["custom"])

# These can always be overridden explicitly via env vars
LLM_BASE_URL: str = os.getenv("LLM_BASE_URL", _preset["base_url"]).rstrip("/")
LLM_MODEL: str = os.getenv("LLM_MODEL", _preset["model"])
LLM_API_KEY: str = os.getenv("LLM_API_KEY", "")
LLM_TEMPERATURE: float = float(os.getenv("LLM_TEMPERATURE", "0.3"))
LLM_TIMEOUT: int = int(os.getenv("LLM_TIMEOUT", "120"))  # seconds

# Optional: separate model for lightweight tasks (scoring, extraction)
# Falls back to LLM_MODEL if not set
LLM_FAST_MODEL: str = os.getenv("LLM_FAST_MODEL", "")



# ── Search (multi-provider with fallback) ─────────────────
# Priority order — first to succeed wins. Comma-separated.
# Options: searxng, serper, apify
SEARCH_PROVIDERS: str = os.getenv("SEARCH_PROVIDERS", "searxng,serper,apify")

# SearXNG — self-hosted metasearch (free)
SEARXNG_URL: str = os.getenv("SEARXNG_URL", "http://localhost:8080")

# Serper.dev — $0.001/query, very reliable (get key at serper.dev)
SERPER_API_KEY: str = os.getenv("SERPER_API_KEY", "")

# ── Apify ───────────────────────────────────────────────────
APIFY_API_TOKEN: str = os.getenv("APIFY_API_TOKEN", "")
APIFY_BASE_URL: str = "https://api.apify.com/v2"

# ── Prospeo (Email Finding & Verification) ─────────────────
PROSPEO_API_KEY: str = os.getenv("PROSPEO_API_KEY", "")
PROSPEO_BASE_URL: str = "https://api.prospeo.io"

# ── FullEnrich (Email Finding via 20+ Provider Waterfall) ──
FULLENRICH_API_KEY: str = os.getenv("FULLENRICH_API_KEY", "")
FULLENRICH_BASE_URL: str = "https://app.fullenrich.com/api/v2"

# ── Apollo.io (B2B Company & Contact Search) ───────────────
APOLLO_API_KEY: str = os.getenv("APOLLO_API_KEY", "")
APOLLO_BASE_URL: str = "https://api.apollo.io"

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

# ── Sending Infrastructure ─────────────────────────────────
# Domains that must NEVER be used for cold outreach (comma-separated)
BLOCKED_SENDING_DOMAINS: list[str] = [
    d.strip() for d in os.getenv("BLOCKED_SENDING_DOMAINS", "").split(",") if d.strip()
]
# Base URL for unsubscribe links (must be publicly accessible in production)
UNSUBSCRIBE_BASE_URL: str = os.getenv("UNSUBSCRIBE_BASE_URL", "http://localhost:8090/unsubscribe")
UNSUBSCRIBE_SERVER_PORT: int = int(os.getenv("UNSUBSCRIBE_SERVER_PORT", "8090"))

# ── Google OAuth ───────────────────────────────────────────
# Create credentials at: https://console.cloud.google.com/apis/credentials
# Application type: Web application
# Redirect URI: http://localhost:8090/oauth/google/callback
GOOGLE_CLIENT_ID: str = os.getenv("GOOGLE_CLIENT_ID", "")
GOOGLE_CLIENT_SECRET: str = os.getenv("GOOGLE_CLIENT_SECRET", "")
GOOGLE_REDIRECT_URI: str = os.getenv("GOOGLE_REDIRECT_URI", "http://localhost:8090/oauth/google/callback")
GOOGLE_OAUTH_SCOPES: list[str] = [
    "https://www.googleapis.com/auth/gmail.send",        # Send emails
    "https://www.googleapis.com/auth/gmail.readonly",    # Read inbox (reply checking)
    "https://www.googleapis.com/auth/userinfo.email",    # Get user's email address
]

# ── Spam Rate Thresholds (Google bulk sender guidelines) ───
# < 0.1%  → healthy target
# 0.1-0.3% → warning / corrective action
# ≥ 0.3% → critical / stop or heavily restrict
SPAM_RATE_HEALTHY: float = float(os.getenv("SPAM_RATE_HEALTHY", "0.001"))    # 0.1%
SPAM_RATE_WARNING: float = float(os.getenv("SPAM_RATE_WARNING", "0.003"))    # 0.3%

# ── Circuit Breaker Thresholds ─────────────────────────────
HARD_BOUNCE_PAUSE_THRESHOLD: float = float(os.getenv("HARD_BOUNCE_PAUSE_THRESHOLD", "0.05"))   # 5% → pause mailbox
DOMAIN_PAUSE_SPAM_THRESHOLD: float = float(os.getenv("DOMAIN_PAUSE_SPAM_THRESHOLD", "0.003"))  # 0.3% → pause domain
BOUNCE_RATE_WARNING: float = float(os.getenv("BOUNCE_RATE_WARNING", "0.03"))                    # 3% → reduce volume
BOUNCE_RATE_CRITICAL: float = float(os.getenv("BOUNCE_RATE_CRITICAL", "0.10"))                  # 10% → pause

# ── Database ───────────────────────────────────────────────
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
raw_db_url = os.getenv("DATABASE_URL", "sqlite:///sales_machine_v2.db")
if raw_db_url.startswith("sqlite:///") and not os.path.isabs(raw_db_url.replace("sqlite:///", "")):
    db_filename = raw_db_url.replace("sqlite:///", "")
    DB_PATH = os.path.join(BASE_DIR, db_filename)
    DATABASE_URL = f"sqlite:///{DB_PATH.replace(os.sep, '/')}"
else:
    DATABASE_URL = raw_db_url
    DB_PATH = raw_db_url.replace("sqlite:///", "")

# ── ICP Scoring ────────────────────────────────────────────
ICP_SCORE_THRESHOLD: int = int(os.getenv("ICP_SCORE_THRESHOLD", "60"))

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
    "employee_10000_plus": -50,  # Penalty for massive enterprises
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

# ── Available Data Sources (Apify Actors) ─────────────────
# Centralized registry of supported Apify actors and primary data sources.
# These actors have been selected for reliability and cost-effectiveness.
AVAILABLE_DATA_SOURCES = {
    "google_maps": {
        "name": "Google Maps (Local Businesses)",
        "actor_id": "compass/google-maps-scraper",
        "description": "Primary source for finding local businesses with physical locations (clinics, restaurants, agencies, etc)."
    },
    "searxng": {
        "name": "SearXNG (Web Search)",
        "actor_id": "self-hosted",
        "description": "General web search engine wrapper for finding websites and directories."
    },
    "apollo": {
        "name": "Apollo.io (B2B Database)",
        "actor_id": "api",
        "description": "SaaS and tech companies. Highly reliable for corporate B2B."
    },
    "linkedin": {
        "name": "LinkedIn Jobs Scraper",
        "actor_id": "rock8/linkedin-jobs-scraper",
        "description": "Finds companies that are currently hiring. Excellent for intent-based outreach."
    },
    "clutch": {
        "name": "Clutch.co Scraper",
        "actor_id": "epctex/clutchco-scraper",
        "description": "B2B service providers (software agencies, marketing, IT services)."
    },
    "yelp": {
        "name": "Yelp Scraper",
        "actor_id": "api-ninja/yelp-ultimate-scraper",
        "description": "Local businesses, primarily in the US (restaurants, home services)."
    },
    "indeed": {
        "name": "Indeed Scraper",
        "actor_id": "kaix/indeed-scraper",
        "description": "Alternative to LinkedIn for finding job postings and hiring intent."
    },
    "upwork": {
        "name": "Upwork Scraper",
        "actor_id": "neatrat/upwork-job-scraper",
        "description": "Companies and individuals actively hiring freelancers or agencies."
    },
    "crunchbase": {
        "name": "Crunchbase Scraper",
        "actor_id": "curious_coder/crunchbase-scraper",
        "description": "Startups and funded companies with recent investment rounds."
    },
    "yellowpages": {
        "name": "Yellow Pages Scraper",
        "actor_id": "delicious_zebu/yellowpages-usa-business-lead-scraper",
        "description": "Traditional local business directory."
    }
}
