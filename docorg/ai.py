import json
import re
from urllib import request


def _extract_json_block(text: str) -> dict | None:
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    m = re.search(r"\{[\s\S]*\}", text)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except json.JSONDecodeError:
        return None


def suggest_date_category(*, text: str, filename: str, categories: list[str], ai_cfg: dict) -> dict | None:
    if not ai_cfg.get("enabled", False):
        return None

    model = ai_cfg.get("model", "mistral:7b-instruct")
    url = ai_cfg.get("ollama_url", "http://localhost:11434").rstrip("/") + "/api/generate"

    prompt = (
        "Extract the most likely document date and category from this document text. "
        "Return ONLY strict JSON with keys: date, category, rationale. "
        "Date must be YYYY-MM-DD or null. Category must be one of: "
        f"{', '.join(categories) if categories else '(none)'} or null.\n\n"
        f"Filename: {filename}\n\n"
        f"Document text:\n{text[:8000]}"
    )

    payload = {
        "model": model,
        "prompt": prompt,
        "stream": False,
        "options": {"temperature": 0.1},
    }

    req = request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    try:
        with request.urlopen(req, timeout=30) as resp:
            body = json.loads(resp.read().decode("utf-8"))
    except Exception:
        return None

    raw = body.get("response", "")
    parsed = _extract_json_block(raw)
    if not parsed:
        return None

    date_value = parsed.get("date")
    category_value = parsed.get("category")
    rationale = parsed.get("rationale")

    if isinstance(category_value, str) and categories:
        normalized = {c.lower(): c for c in categories}
        category_value = normalized.get(category_value.lower())

    return {
        "date": date_value if isinstance(date_value, str) else None,
        "category": category_value if isinstance(category_value, str) else None,
        "rationale": rationale if isinstance(rationale, str) else "",
    }
