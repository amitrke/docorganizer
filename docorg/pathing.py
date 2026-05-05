from __future__ import annotations

from pathlib import Path


def is_host_neutral_path(stored_path: str) -> bool:
    """Return True when a DB filepath uses host-neutral roots."""
    normalized = stored_path.replace("\\", "/")
    return normalized.startswith("documents/") or normalized.startswith("inbox/")


def to_stored_path(path: str | Path, cfg: dict) -> str:
    """Convert an absolute/local path into a host-neutral DB path when possible.

    Preferred DB formats:
    - inbox/<relative path>
    - documents/<relative path>
    Falls back to the original path string if it doesn't live under configured roots.
    """
    candidate = Path(path)
    candidate_abs = candidate.resolve()

    roots = {
        "inbox": Path(cfg["paths"]["inbox"]).resolve(),
        "documents": Path(cfg["paths"]["documents"]).resolve(),
    }

    for root_name, root_path in roots.items():
        try:
            rel = candidate_abs.relative_to(root_path)
            return f"{root_name}/{rel.as_posix()}"
        except ValueError:
            continue

    return str(path)


def resolve_stored_path(stored_path: str, cfg: dict) -> Path:
    """Resolve a DB filepath into a concrete local filesystem path.

    Host-neutral DB formats are resolved against configured roots.
    """
    normalized = stored_path.replace("\\", "/")

    if normalized.startswith("documents/"):
        suffix = normalized[len("documents/"):]
        return (Path(cfg["paths"]["documents"]) / Path(suffix)).resolve()

    if normalized.startswith("inbox/"):
        suffix = normalized[len("inbox/"):]
        return (Path(cfg["paths"]["inbox"]) / Path(suffix)).resolve()

    path = Path(stored_path)
    if path.is_absolute():
        return path.resolve()

    return (Path.cwd() / path).resolve()
