import json
import re
from urllib import request


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


def suggest_date_category(*, text: str, filename: str, categories: list[str], ai_cfg: dict) -> dict | None:
    """Returns suggestion dict on success, or None. Sets suggest_date_category.last_error on failure."""
    suggest_date_category.last_error = ""
    if not ai_cfg.get("enabled", False):
        suggest_date_category.last_error = "ai.enabled is false in config"
        return None

    model = ai_cfg.get("model", "mistral:7b-instruct")
    url = ai_cfg.get("ollama_url", "http://localhost:11434").rstrip("/") + "/api/generate"

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

    timeout = ai_cfg.get("timeout", 120)
    try:
        with request.urlopen(req, timeout=timeout) as resp:
            body = json.loads(resp.read().decode("utf-8"))
    except Exception as exc:
        suggest_date_category.last_error = f"Ollama request failed: {exc}"
        return None

    raw = body.get("response", "")
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
