import json
import re
import sqlite3
from urllib import request
from urllib.error import HTTPError

OPENROUTER_REFERER = "https://github.com/amitrke/docorganizer"
OPENROUTER_TITLE = "docorganizer"

PROVIDER_DEFAULTS: dict[str, dict[str, object]] = {
    "ollama": {"base_url": "http://localhost:11434", "requires_key": False},
    "openrouter": {"base_url": "https://openrouter.ai/api/v1", "requires_key": True},
    "nvidia": {"base_url": "https://integrate.api.nvidia.com/v1", "requires_key": True},
    "mistral": {"base_url": "https://api.mistral.ai/v1", "requires_key": True},
    "deepseek": {"base_url": "https://api.deepseek.com", "requires_key": True},
    # Google's OpenAI-compatibility layer for Gemini — same request/response shape
    # as everything else here, so no bespoke client needed.
    "gemini": {"base_url": "https://generativelanguage.googleapis.com/v1beta/openai", "requires_key": True},
    "poe": {"base_url": "https://api.poe.com/v1", "requires_key": True},
    # No fixed default: covers OpenAI itself, self-hosted LiteLLM/vLLM/llama.cpp/LM
    # Studio, or any other OpenAI-compatible endpoint. Base URL is required for this one.
    "custom": {"base_url": "", "requires_key": False},
}

DEFAULT_AI_SETTINGS: dict[str, object] = {
    "enabled": False,
    "provider": "ollama",
    # Left blank rather than a concrete URL: _call_ollama/_call_openai_compatible fall
    # back to PROVIDER_DEFAULTS[provider] whenever this is empty, so a blank base_url
    # always tracks whichever provider is currently selected instead of going stale
    # when the provider is switched.
    "base_url": "",
    "api_key": "",
    "model": "mistral:7b-instruct",
    "timeout": 180,
    "max_tokens": 1200,
}

_AI_SETTINGS_KEY = "ai_config"


def load_ai_settings(conn: sqlite3.Connection) -> dict | None:
    """Return the web-configured AI settings, or None if nothing has been saved yet."""
    from .database import get_app_setting

    raw = get_app_setting(conn, _AI_SETTINGS_KEY)
    if raw is None:
        return None
    try:
        stored = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if not isinstance(stored, dict):
        return None
    merged = dict(DEFAULT_AI_SETTINGS)
    merged.update(stored)
    return merged


def save_ai_settings(conn: sqlite3.Connection, data: dict) -> None:
    from .database import set_app_setting

    merged = dict(DEFAULT_AI_SETTINGS)
    merged.update(data)
    set_app_setting(conn, _AI_SETTINGS_KEY, json.dumps(merged))


def resolve_ai_config(cfg: dict, conn: sqlite3.Connection | None = None) -> dict:
    """Web-configured DB settings take priority; falls back to config.yaml's ai: block."""
    if conn is not None:
        db_settings = load_ai_settings(conn)
        if db_settings is not None:
            return db_settings
    return cfg.get("ai", {})


def _repair_truncated_json(text: str) -> str | None:
    """Best-effort repair for a JSON object cut off mid-generation."""
    text = text.strip()
    if not text.startswith("{"):
        return None

    repaired: list[str] = []
    stack: list[str] = []
    in_string = False
    escaped = False

    for char in text:
        repaired.append(char)

        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue

        if char == '"':
            in_string = True
        elif char in "{[":
            stack.append(char)
        elif char in "}]":
            if stack:
                opener = stack[-1]
                if (opener == "{" and char == "}") or (opener == "[" and char == "]"):
                    stack.pop()
            if not stack:
                break

    if in_string:
        repaired.append('"')

    while stack:
        opener = stack.pop()
        repaired.append("}" if opener == "{" else "]")

    return "".join(repaired)


def _extract_json_block(text: str) -> dict | None:
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    start = text.find("{")
    if start == -1:
        return None

    candidate = text[start:]
    try:
        return json.loads(candidate)
    except json.JSONDecodeError:
        repaired = _repair_truncated_json(candidate)
        if not repaired:
            return None
        try:
            return json.loads(repaired)
        except json.JSONDecodeError:
            return None


def _normalize_fields(value: object) -> dict[str, str]:
    if not isinstance(value, dict):
        return {}

    normalized: dict[str, str] = {}
    for key, raw_value in value.items():
        if not isinstance(key, str):
            continue
        field_key = re.sub(r"[^a-z0-9]+", "_", key.strip().lower()).strip("_")
        if not field_key or raw_value is None:
            continue
        if isinstance(raw_value, bool):
            field_value = "true" if raw_value else "false"
        elif isinstance(raw_value, (int, float, str)):
            field_value = str(raw_value).strip()
        else:
            continue
        if field_value:
            normalized[field_key] = field_value
    return normalized


def _call_ollama(ai_cfg: dict, prompt: str, timeout: int) -> str:
    model = ai_cfg.get("model", "mistral:7b-instruct")
    base_url = ai_cfg.get("base_url") or ai_cfg.get("ollama_url", "http://localhost:11434")
    url = base_url.rstrip("/") + "/api/generate"

    payload = {
        "model": model,
        "prompt": prompt,
        "format": "json",
        "stream": False,
        "options": {
            "temperature": 0.1,
            "num_predict": int(ai_cfg.get("max_tokens", 768)),
        },
    }
    req = request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with request.urlopen(req, timeout=timeout) as resp:
        body = json.loads(resp.read().decode("utf-8"))
    return str(body.get("response", ""))


def _normalize_api_keys(raw: str | None) -> list[str]:
    """Split a comma/newline-separated key string for round-robin rotation on 429s."""
    if not raw:
        return []
    parts = [p.strip() for p in raw.replace("\n", ",").split(",")]
    return [p for p in parts if p]


def _call_openai_compatible(ai_cfg: dict, prompt: str, timeout: int) -> str:
    provider = ai_cfg.get("provider", "")
    model = ai_cfg.get("model", "")
    defaults = PROVIDER_DEFAULTS.get(provider, {})
    base_url = ai_cfg.get("base_url") or defaults.get("base_url", "")
    url = base_url.rstrip("/") + "/chat/completions"

    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.1,
        "max_tokens": int(ai_cfg.get("max_tokens", 768)),
    }
    headers = {"Content-Type": "application/json"}
    if provider == "openrouter":
        # OpenRouter uses these for attribution/rankings; harmless elsewhere but scoped anyway.
        headers["HTTP-Referer"] = OPENROUTER_REFERER
        headers["X-Title"] = OPENROUTER_TITLE

    keys = _normalize_api_keys(ai_cfg.get("api_key", "")) or [""]
    body: dict = {}
    for key in keys:
        req = request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={**headers, "Authorization": f"Bearer {key}"},
            method="POST",
        )
        try:
            with request.urlopen(req, timeout=timeout) as resp:
                body = json.loads(resp.read().decode("utf-8"))
            break
        except HTTPError as exc:
            if exc.code == 429 and key != keys[-1]:
                continue  # rotate to the next key on rate limit
            raise

    choices = body.get("choices") or []
    if not choices:
        return ""
    return str(choices[0].get("message", {}).get("content", ""))


def _call_provider(ai_cfg: dict, prompt: str) -> str:
    """Dispatch to the configured AI provider. Raises on transport/HTTP failure."""
    timeout = ai_cfg.get("timeout", 120)
    provider = ai_cfg.get("provider", "ollama")
    if provider == "ollama":
        return _call_ollama(ai_cfg, prompt, timeout)
    return _call_openai_compatible(ai_cfg, prompt, timeout)


def test_provider_connection(ai_cfg: dict) -> tuple[bool, str]:
    """Send a trivial prompt to verify provider connectivity/credentials. Returns (ok, message)."""
    try:
        raw = _call_provider(ai_cfg, "Reply with the single word: OK")
    except Exception as exc:
        provider = ai_cfg.get("provider", "ollama")
        return False, f"{provider} request failed: {exc}"
    snippet = raw.strip()[:200] or "(empty response)"
    return True, f"Connected. Model responded: {snippet}"


def suggest_date_category(*, text: str, filename: str, categories: list[str], ai_cfg: dict) -> dict | None:
    """Returns suggestion dict on success, or None. Sets suggest_date_category.last_error on failure."""
    suggest_date_category.last_error = ""
    if not ai_cfg.get("enabled", False):
        suggest_date_category.last_error = "ai.enabled is false in config"
        return None

    prompt = (
        "Extract the most likely document date and category from this document text. "
        "Return ONLY strict JSON with keys: date, category, rationale, summary, fields. "
        "Date must be YYYY-MM-DD or null. Category must be one of: "
        f"{', '.join(categories) if categories else '(none)'} or null. "
        "Rationale should briefly explain the evidence behind the date and category. "
        "Summary should be a detailed 3-6 sentence summary of the document contents. "
        "Fields must be a JSON object of explicit document facts using snake_case keys such as "
        "total_amount, location, invoice_number, account_number, provider_name, due_date, or tax_year. "
        "Do not invent values; omit uncertain fields.\n\n"
        f"Filename: {filename}\n\n"
        f"Document text:\n{text[:8000]}"
    )

    try:
        raw = _call_provider(ai_cfg, prompt)
    except Exception as exc:
        provider = ai_cfg.get("provider", "ollama")
        suggest_date_category.last_error = f"{provider} request failed: {exc}"
        return None

    parsed = _extract_json_block(raw)
    if not parsed:
        suggest_date_category.last_error = f"Could not parse JSON from model response: {raw[:200]!r}"
        return None

    date_value = parsed.get("date")
    category_value = parsed.get("category")
    rationale = parsed.get("rationale")
    summary = parsed.get("summary")
    fields = _normalize_fields(parsed.get("fields"))

    if isinstance(category_value, str) and categories:
        normalized = {c.lower(): c for c in categories}
        category_value = normalized.get(category_value.lower())

    return {
        "date": date_value if isinstance(date_value, str) else None,
        "category": category_value if isinstance(category_value, str) else None,
        "rationale": rationale if isinstance(rationale, str) else "",
        "summary": summary if isinstance(summary, str) else "",
        "fields": fields,
    }
