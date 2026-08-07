"""
utils.py — Shared utilities: logging, retry, JSON helpers.
"""

import json
import logging
import os
import time
import functools
from logging.handlers import RotatingFileHandler
from datetime import datetime, timezone

from config import LOG_LEVEL, LOG_FILE


# ── Logging Setup ──────────────────────────────────────────
def get_logger(name: str) -> logging.Logger:
    """
    Get a configured logger.

    Logs to both stdout and a rotating file.
    Format: [2026-08-06 19:42:03] [module_name] [LEVEL] Message
    """
    logger = logging.getLogger(name)

    if logger.handlers:
        return logger  # Already configured

    logger.setLevel(getattr(logging, LOG_LEVEL.upper(), logging.INFO))

    formatter = logging.Formatter(
        "[%(asctime)s] [%(name)s] [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # Console handler
    console = logging.StreamHandler()
    console.setFormatter(formatter)
    logger.addHandler(console)

    # File handler (rotating, 10MB max, keep 5 backups)
    log_dir = os.path.dirname(LOG_FILE)
    if log_dir:
        os.makedirs(log_dir, exist_ok=True)

    file_handler = RotatingFileHandler(
        LOG_FILE, maxBytes=10 * 1024 * 1024, backupCount=5
    )
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    return logger


# ── Retry Decorator ────────────────────────────────────────
def retry(max_attempts: int = 3, base_delay: float = 1.0, backoff: float = 2.0):
    """
    Exponential backoff retry decorator.

    Usage:
        @retry(max_attempts=3)
        def flaky_api_call():
            ...
    """
    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            logger = get_logger(func.__module__)
            last_exception = None
            for attempt in range(1, max_attempts + 1):
                try:
                    return func(*args, **kwargs)
                except Exception as e:
                    last_exception = e
                    if attempt < max_attempts:
                        delay = base_delay * (backoff ** (attempt - 1))
                        logger.warning(
                            f"{func.__name__} attempt {attempt}/{max_attempts} failed: {e}. "
                            f"Retrying in {delay:.1f}s..."
                        )
                        time.sleep(delay)
                    else:
                        logger.error(
                            f"{func.__name__} failed after {max_attempts} attempts: {e}"
                        )
            raise last_exception
        return wrapper
    return decorator


# ── JSON Helpers ───────────────────────────────────────────
def safe_json_loads(text: str) -> dict | list | None:
    """
    Try to parse JSON from text, stripping markdown fences if present.

    Returns parsed object or None on failure.
    """
    if not text:
        return None

    cleaned = text.strip()

    # Strip markdown code fences
    if cleaned.startswith("```"):
        lines = cleaned.split("\n")
        # Remove first and last lines if they're fences
        if lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        cleaned = "\n".join(lines).strip()

    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        return None


def safe_json_dumps(obj) -> str:
    """Serialize to compact JSON string."""
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"))


# ── Domain Extraction ─────────────────────────────────────
def extract_domain(url: str) -> str:
    """Extract root domain from URL. e.g. 'https://www.example.com/about' → 'example.com'"""
    if not url:
        return ""
    url = url.lower().strip()
    # Remove protocol
    for prefix in ("https://", "http://", "www."):
        if url.startswith(prefix):
            url = url[len(prefix):]
    # Remove path
    url = url.split("/")[0]
    return url


# ── Email Pattern Guessing ─────────────────────────────────
def guess_email(first_name: str, last_name: str, domain: str) -> list[str]:
    """
    Generate common email pattern guesses.

    Returns list of candidates, most-likely first.
    """
    first = first_name.lower().strip()
    last = last_name.lower().strip()
    d = domain.lower().strip()

    if not first or not last or not d:
        return []

    return [
        f"{first}@{d}",
        f"{first}.{last}@{d}",
        f"{first[0]}{last}@{d}",
        f"{first}{last[0]}@{d}",
        f"{first}_{last}@{d}",
        f"{last}@{d}",
    ]


# ── Timestamp Helper ──────────────────────────────────────
def utcnow() -> datetime:
    """Return timezone-aware UTC now."""
    return datetime.now(timezone.utc)
