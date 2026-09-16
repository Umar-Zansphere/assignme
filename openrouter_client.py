"""
openrouter_client.py — Universal LLM client (multi-provider).

Supports any OpenAI-compatible API:
  - Ollama (local, free)
  - Groq (fast cloud inference)
  - OpenRouter (100+ models)
  - OpenAI (GPT-4o, etc.)
  - DeepSeek
  - Any custom endpoint

Provider is selected via LLM_PROVIDER in .env. No code changes needed.

Usage:
    from openrouter_client import call_llm

    result = call_llm(
        system_prompt="You are a research assistant.",
        user_prompt="Summarize this company: ...",
    )
    # result is a parsed dict/list (JSON)
"""

import httpx

import config
from utils import get_logger, safe_json_loads, retry

log = get_logger("llm")


class LLMError(Exception):
    """Raised when LLM API call fails after retries."""
    pass

# Keep backward-compatible alias
OpenRouterError = LLMError


def _build_headers() -> dict:
    """Build request headers based on the configured provider."""
    headers = {
        "Content-Type": "application/json",
    }

    if config.LLM_API_KEY:
        headers["Authorization"] = f"Bearer {config.LLM_API_KEY}"

    # Provider-specific headers
    if config.LLM_PROVIDER == "openrouter":
        headers["HTTP-Referer"] = "https://zansphere.com"
        headers["X-Title"] = "Zansphere Sales Machine"
    elif config.LLM_PROVIDER == "groq":
        # Groq uses standard Bearer auth, no extra headers needed
        pass

    return headers


def _build_payload(messages: list[dict], model: str) -> dict:
    """Build the request payload with provider-appropriate parameters."""
    payload = {
        "model": model,
        "messages": messages,
        "temperature": config.LLM_TEMPERATURE,
    }

    # Groq free-tier has strict OTPM limits — cap output to stay under
    if config.LLM_PROVIDER == "groq":
        payload["max_tokens"] = 800

    # Provider-specific tweaks — JSON mode
    if config.LLM_PROVIDER in ("groq", "openai", "gemini", "deepseek"):
        payload["response_format"] = {"type": "json_object"}

    return payload


@retry(max_attempts=5, base_delay=5.0)
def _raw_chat(messages: list[dict], model: str) -> str:
    """Send chat completion request and return raw content string."""
    headers = _build_headers()
    payload = _build_payload(messages, model)
    url = f"{config.LLM_BASE_URL}/chat/completions"

    log.debug(f"POST {url} | provider={config.LLM_PROVIDER} model={model}")

    with httpx.Client(timeout=float(config.LLM_TIMEOUT)) as client:
        response = client.post(url, headers=headers, json=payload)

        # Handle rate limits gracefully
        if response.status_code == 429:
            retry_after = response.headers.get("retry-after", "30")
            raise LLMError(f"Rate limited by {config.LLM_PROVIDER}. Retry after {retry_after}s")

        response.raise_for_status()

    data = response.json()

    # Standard OpenAI response format
    try:
        content = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError) as e:
        raise LLMError(f"Unexpected response structure from {config.LLM_PROVIDER}: {e}\nResponse: {data}")

    return content


def call_llm(
    system_prompt: str,
    user_prompt: str,
    model: str | None = None,
    expect_json: bool = True,
    fast: bool = False,
) -> dict | list | str:
    """
    Call LLM and return structured response.

    Args:
        system_prompt: System-level instruction.
        user_prompt: User message / data to process.
        model: Override model (defaults to config).
        expect_json: If True, parse response as JSON. Re-prompt once if invalid.
        fast: If True, use LLM_FAST_MODEL for lightweight tasks (falls back to LLM_MODEL).

    Returns:
        Parsed JSON (dict/list) if expect_json=True, else raw string.

    Raises:
        LLMError: If API fails or JSON parsing fails after retry.
    """
    if model:
        selected_model = model
    elif fast and config.LLM_FAST_MODEL:
        selected_model = config.LLM_FAST_MODEL
    else:
        selected_model = config.LLM_MODEL

    # Groq strictly requires the word 'json' in the prompt when using JSON mode
    if expect_json and "json" not in system_prompt.lower() and "json" not in user_prompt.lower():
        system_prompt = system_prompt.rstrip() + " Please respond in JSON format."

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]

    log.info(f"[{config.LLM_PROVIDER}] Calling {selected_model} (json={expect_json})")
    content = _raw_chat(messages, selected_model)

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

    content = _raw_chat(messages, selected_model)
    parsed = safe_json_loads(content)
    if parsed is not None:
        log.info("LLM returned valid JSON on retry")
        return parsed

    raise LLMError(f"LLM failed to return valid JSON after retry. Raw: {content[:500]}")


def call_llm_with_schema(
    system_prompt: str,
    user_prompt: str,
    required_keys: list[str],
    model: str | None = None,
) -> dict:
    """
    Call LLM and validate that response contains required keys.

    Raises LLMError if keys are missing.
    """
    result = call_llm(system_prompt, user_prompt, model=model, expect_json=True)

    if not isinstance(result, dict):
        raise LLMError(f"Expected dict, got {type(result).__name__}")

    missing = [k for k in required_keys if k not in result]
    if missing:
        raise LLMError(f"LLM response missing required keys: {missing}")

    return result


def get_provider_info() -> dict:
    """Return current LLM configuration for dashboard display."""
    return {
        "provider": config.LLM_PROVIDER,
        "model": config.LLM_MODEL,
        "fast_model": config.LLM_FAST_MODEL or config.LLM_MODEL,
        "base_url": config.LLM_BASE_URL,
        "has_api_key": bool(config.LLM_API_KEY),
        "temperature": config.LLM_TEMPERATURE,
        "timeout": config.LLM_TIMEOUT,
    }


if __name__ == "__main__":
    # Quick test — shows which provider is active
    info = get_provider_info()
    print(f"Provider:  {info['provider']}")
    print(f"Model:     {info['model']}")
    print(f"Base URL:  {info['base_url']}")
    print(f"API Key:   {'✅ Set' if info['has_api_key'] else '❌ Not set (OK for Ollama)'}")
    print()

    result = call_llm(
        system_prompt="You are a helpful assistant. Respond in JSON only.",
        user_prompt='Return a JSON object with keys "status" and "message". Set status to "ok" and message to "LLM client is working".',
    )
    print(f"Result: {result}")
