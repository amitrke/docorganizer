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

# Legacy single-config key (pre-profiles). Only read once, as a migration
# source, when the ai_profiles table is still empty.
_LEGACY_AI_SETTINGS_KEY = "ai_config"


def _migrate_legacy_ai_config(cfg: dict, conn: sqlite3.Connection) -> None:
    """One-time migration into a profile row: prefers the old single DB config,
    falls back to config.yaml's ai: block. No-op if neither exists."""
    from .database import get_app_setting, insert_ai_profile

    legacy: dict = {}
    was_active = False

    raw = get_app_setting(conn, _LEGACY_AI_SETTINGS_KEY)
    if raw:
        try:
            stored = json.loads(raw)
        except json.JSONDecodeError:
            stored = None
        if isinstance(stored, dict):
            legacy = stored
            was_active = bool(stored.get("enabled", False))

    if not legacy:
        legacy_cfg = cfg.get("ai", {}) or {}
        if legacy_cfg:
            legacy = {
                "provider": "ollama",
                "model": legacy_cfg.get("model", DEFAULT_AI_SETTINGS["model"]),
                "base_url": legacy_cfg.get("ollama_url", ""),
                "api_key": "",
                "timeout": legacy_cfg.get("timeout", DEFAULT_AI_SETTINGS["timeout"]),
                "max_tokens": legacy_cfg.get("max_tokens", DEFAULT_AI_SETTINGS["max_tokens"]),
            }
            was_active = bool(legacy_cfg.get("enabled", False))

    if not legacy:
        return

    insert_ai_profile(
        conn,
        name="Default",
        provider=legacy.get("provider", "ollama"),
        model=legacy.get("model") or DEFAULT_AI_SETTINGS["model"],
        base_url=legacy.get("base_url", ""),
        api_key=legacy.get("api_key", ""),
        timeout=int(legacy.get("timeout") or DEFAULT_AI_SETTINGS["timeout"]),
        max_tokens=int(legacy.get("max_tokens") or DEFAULT_AI_SETTINGS["max_tokens"]),
        is_active=was_active,
    )


def ensure_ai_profiles_seeded(cfg: dict, conn: sqlite3.Connection) -> None:
    """Migrate the legacy single-config/config.yaml seed into a profile, once,
    if the ai_profiles table doesn't have any rows yet. No-op otherwise."""
    from .database import list_ai_profiles

    if not list_ai_profiles(conn):
        _migrate_legacy_ai_config(cfg, conn)


def resolve_ai_config(cfg: dict, conn: sqlite3.Connection) -> dict | None:
    """Settings for the active AI profile, or None if no profile is active."""
    from .database import get_active_ai_profile

    ensure_ai_profiles_seeded(cfg, conn)
    profile = get_active_ai_profile(conn)
    if profile is None:
        return None
    return {
        "provider": profile["provider"],
        "model": profile["model"],
        "base_url": profile["base_url"],
        "api_key": profile["api_key"],
        "timeout": profile["timeout"],
        "max_tokens": profile["max_tokens"],
    }


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
    """Returns suggestion dict on success, or None. Sets suggest_date_category.last_error on failure.

    Callers are responsible for checking there's an AI profile to use at all
    (e.g. via resolve_ai_config returning non-None) before calling this.
    """
    suggest_date_category.last_error = ""

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
