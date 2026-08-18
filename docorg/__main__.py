"""Container entrypoint: load config, ensure the DB exists, start the web server."""
import os
from pathlib import Path

import uvicorn

from .config import load_config
from .database import init_db
from .web import create_app


def main() -> None:
    config_path = Path(os.environ.get("DOCORG_CONFIG", "config.yaml"))
    host = os.environ.get("DOCORG_HOST", "0.0.0.0")
    port = int(os.environ.get("DOCORG_PORT", "8000"))

    cfg = load_config(config_path)
    init_db(cfg["paths"]["database"])

    uvicorn.run(create_app(cfg, config_path=config_path), host=host, port=port)


if __name__ == "__main__":
    main()
