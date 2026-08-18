from pathlib import Path
import yaml

DEFAULT_CONFIG_PATH = Path("config.yaml")


def load_config(path: Path = DEFAULT_CONFIG_PATH) -> dict:
    with open(path) as f:
        cfg = yaml.safe_load(f)

    # Resolve relative paths against the config file's directory
    base = Path(path).parent
    for key in ("inbox", "documents", "database"):
        raw = cfg.get("paths", {}).get(key)
        if raw:
            resolved = base / raw
            cfg["paths"][key] = str(resolved)

    return cfg


def get_configured_categories(cfg_path: Path) -> list[str]:
    """Read the `categories:` list straight from the config file (no path resolution)."""
    with open(cfg_path) as f:
        raw = yaml.safe_load(f)
    return raw.get("categories", []) if raw else []


def add_configured_category(cfg_path: Path, name: str) -> bool:
    """Append a category to the config file. Returns False if it already existed."""
    with open(cfg_path) as f:
        raw = yaml.safe_load(f)
    cats: list = raw.setdefault("categories", [])
    if name in cats:
        return False
    cats.append(name)
    with open(cfg_path, "w") as f:
        yaml.dump(raw, f, default_flow_style=False, allow_unicode=True)
    return True


def remove_configured_category(cfg_path: Path, name: str) -> bool:
    """Remove a category from the config file. Returns False if it wasn't present."""
    with open(cfg_path) as f:
        raw = yaml.safe_load(f)
    cats: list = raw.get("categories", [])
    if name not in cats:
        return False
    cats.remove(name)
    with open(cfg_path, "w") as f:
        yaml.dump(raw, f, default_flow_style=False, allow_unicode=True)
    return True
