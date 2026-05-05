import re
from datetime import date, datetime
from pathlib import Path

# ---------------------------------------------------------------------------
# Regex patterns — ordered from most specific to least specific.
# Phase 3 will expand this list with keyword-prefixed patterns.
# ---------------------------------------------------------------------------

_PATTERNS: list[tuple[str, str]] = [
    # ISO: 2024-03-15
    (r"\b(\d{4})-(\d{2})-(\d{2})\b", "ymd"),
    # DD/MM/YYYY or DD-MM-YYYY
    (r"\b(\d{2})[/\-](\d{2})[/\-](\d{4})\b", "dmy"),
    # DD Month YYYY  e.g. 15 March 2024
    (
        r"\b(\d{1,2})\s+"
        r"(January|February|March|April|May|June|July|August|September|October|November|December)"
        r"\s+(\d{4})\b",
        "dmonthy",
    ),
    # Month DD, YYYY  e.g. March 15, 2024
    (
        r"\b(January|February|March|April|May|June|July|August|September|October|November|December)"
        r"\s+(\d{1,2}),?\s+(\d{4})\b",
        "monthdY",
    ),
]

_MONTH_MAP = {
    "january": 1, "february": 2, "march": 3, "april": 4,
    "may": 5, "june": 6, "july": 7, "august": 8,
    "september": 9, "october": 10, "november": 11, "december": 12,
}


def _parse_match(m: re.Match, fmt: str) -> date | None:
    try:
        if fmt == "ymd":
            return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        if fmt == "dmy":
            return date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
        if fmt == "dmonthy":
            month = _MONTH_MAP[m.group(2).lower()]
            return date(int(m.group(3)), month, int(m.group(1)))
        if fmt == "monthdY":
            month = _MONTH_MAP[m.group(1).lower()]
            return date(int(m.group(3)), month, int(m.group(2)))
    except (ValueError, KeyError):
        return None
    return None


def detect_date(text: str, file_path: str | Path | None = None) -> tuple[date | None, int]:
    """
    Attempt to detect a document date from extracted text.

    Returns:
        (detected_date, candidate_count)
        detected_date is None if no valid date was found in text.
        candidate_count is useful for confidence display in the TUI.

    Falls back to the file's modification date only when file_path is given
    and no date is found in the text.
    """
    candidates: list[date] = []

    for pattern, fmt in _PATTERNS:
        for m in re.finditer(pattern, text, re.IGNORECASE):
            d = _parse_match(m, fmt)
            if d and date(1990, 1, 1) <= d <= date(2100, 12, 31):
                candidates.append(d)

    if candidates:
        # Use the earliest plausible date — most likely to be the document date
        # rather than a future reference date embedded in the text.
        return min(candidates), len(candidates)

    # Fallback: file modification date (F10)
    if file_path is not None:
        mtime = Path(file_path).stat().st_mtime
        return datetime.fromtimestamp(mtime).date(), 0

    return None, 0
