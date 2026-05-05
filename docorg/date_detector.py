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


def _filename_date(file_path: str | Path | None) -> date | None:
    """Extract an unambiguous date from the filename, if present."""
    if file_path is None:
        return None

    name = Path(file_path).stem

    # YYYYMMDD / YYYY-MM-DD / YYYY_MM_DD
    for m in re.finditer(r"(?<!\d)(\d{4})[\-_]?(\d{2})[\-_]?(\d{2})(?!\d)", name):
        try:
            d = date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            continue
        if date(1990, 1, 1) <= d <= date(2100, 12, 31):
            return d

    # MMDDYYYY / MM-DD-YYYY / MM_DD_YYYY
    for m in re.finditer(r"(?<!\d)(\d{2})[\-_]?(\d{2})[\-_]?((?:19|20)\d{2})(?!\d)", name):
        try:
            d = date(int(m.group(3)), int(m.group(1)), int(m.group(2)))
        except ValueError:
            continue
        if date(1990, 1, 1) <= d <= date(2100, 12, 31):
            return d

    return None


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

    Detection order:
      1) Filename date patterns (high confidence)
      2) Text date patterns
      3) File modification date fallback when file_path is given
    """
    filename_date = _filename_date(file_path)
    if filename_date is not None:
        return filename_date, 1

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
