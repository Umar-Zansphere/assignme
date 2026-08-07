"""
openrouter_client.py — Reusable LLM client via OpenRouter API.

Usage:
    from openrouter_client import call_llm

    result = call_llm(
        system_prompt="You are a research assistant.",
        user_prompt="Summarize this company: ...",
    )
    # result is a parsed dict/list (JSON)
"""

import httpx

from config import OPENROUTER_API_KEY, OPENROUTER_MODEL, OPENROUTER_BASE_URL, OPENROUTER_MAX_RETRIES
from utils import get_logger, safe_json_loads, retry

log = get_logger("openrouter")


class OpenRouterError(Exception):
    """Raised when OpenRouter API call fails after retries."""
    pass


@retry(max_attempts=5, base_delay=5.0)
def _raw_chat(messages: list[dict], model: str) -> str:
    """Send chat completion request and return raw content string."""
    if not OPENROUTER_API_KEY:
        raise OpenRouterError("OPENROUTER_API_KEY is not set in .env")

    with httpx.Client(timeout=60.0) as client:
        response = client.post(
            f"{OPENROUTER_BASE_URL}/chat/completions",
            headers={
                "Authorization": f"Bearer {OPENROUTER_API_KEY}",
                "Content-Type": "application/json",
                "HTTP-Referer": "https://sales-machine.local",
            },
            json={
                "model": model,
                "messages": messages,
                "temperature": 0.3,
            },
        )
        response.raise_for_status()

    data = response.json()
    content = data["choices"][0]["message"]["content"]
    return content


def call_llm(
    system_prompt: str,
    user_prompt: str,
    model: str | None = None,
    expect_json: bool = True,
) -> dict | list | str:
    """
    Call OpenRouter LLM and return structured response.

    Args:
        system_prompt: System-level instruction.
        user_prompt: User message / data to process.
        model: Override model (defaults to config).
        expect_json: If True, parse response as JSON. Re-prompt once if invalid.

    Returns:
        Parsed JSON (dict/list) if expect_json=True, else raw string.

    Raises:
        OpenRouterError: If API fails or JSON parsing fails after retry.
    """
    model = model or OPENROUTER_MODEL

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]

    log.info(f"Calling {model} (json={expect_json})")
    content = _raw_chat(messages, model)

    if not expect_json:
        return content

    # Try to parse JSON
    parsed = safe_json_loads(content)
    if parsed is not None:
        log.info("LLM returned valid JSON")
        return parsed

    # Re-prompt once asking for valid JSON
    log.warning("LLM returned invalid JSON, re-prompting...")
    messages.append({"role": "assistant", "content": content})
    messages.append({
        "role": "user",
        "content": "Your response was not valid JSON. Please respond with ONLY valid JSON, no markdown fences or extra text.",
    })

    content = _raw_chat(messages, model)
    parsed = safe_json_loads(content)
    if parsed is not None:
        log.info("LLM returned valid JSON on retry")
        return parsed

    raise OpenRouterError(f"LLM failed to return valid JSON after retry. Raw: {content[:500]}")


def call_llm_with_schema(
    system_prompt: str,
    user_prompt: str,
    required_keys: list[str],
    model: str | None = None,
) -> dict:
    """
    Call LLM and validate that response contains required keys.

    Raises OpenRouterError if keys are missing.
    """
    result = call_llm(system_prompt, user_prompt, model=model, expect_json=True)

    if not isinstance(result, dict):
        raise OpenRouterError(f"Expected dict, got {type(result).__name__}")

    missing = [k for k in required_keys if k not in result]
    if missing:
        raise OpenRouterError(f"LLM response missing required keys: {missing}")

    return result


if __name__ == "__main__":
    # Quick test
    result = call_llm(
        system_prompt="You are a helpful assistant. Respond in JSON only.",
        user_prompt='Return a JSON object with keys "status" and "message". Set status to "ok" and message to "OpenRouter client is working".',
    )
    print(result)
