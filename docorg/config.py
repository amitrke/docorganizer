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
