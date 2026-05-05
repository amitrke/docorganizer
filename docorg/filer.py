import re
import shutil
from pathlib import Path


def _safe_dest(target_dir: Path, filename: str) -> Path:
    """
    Return a destination path that does not already exist.
    If <filename> is taken, append _2, _3, … to the stem (F13a).
    """
    dest = target_dir / filename
    if not dest.exists():
        return dest

    stem = Path(filename).stem
    suffix = Path(filename).suffix
    counter = 2
    while True:
        dest = target_dir / f"{stem}_{counter}{suffix}"
        if not dest.exists():
            return dest
        counter += 1


def file_document(
    src: str | Path,
    *,
    documents_root: str | Path,
    doc_date,          # datetime.date
    category: str | None = None,
) -> Path:
    """
    Move *src* PDF to:
        <documents_root>/YYYY/MM/            (no category)
        <documents_root>/YYYY/MM/<category>/ (with category)

    Creates the target folder if needed (F13).
    Returns the final destination path.
    """
    src = Path(src)
    documents_root = Path(documents_root)

    year = doc_date.strftime("%Y")
    month = doc_date.strftime("%m")

    parts = [documents_root, year, month]
    if category:
        # Sanitise: lowercase, replace spaces/special chars with underscores
        safe_cat = re.sub(r"[^\w]", "_", category.strip().lower())
        parts.append(safe_cat)

    target_dir = Path(*parts)
    target_dir.mkdir(parents=True, exist_ok=True)

    dest = _safe_dest(target_dir, src.name)
    shutil.move(str(src), dest)
    return dest
