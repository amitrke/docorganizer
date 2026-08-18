from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

import yaml


@dataclass
class Settings:
    db_path: str
    documents_path: str
    ollama_url: str
    ollama_model: str
    ollama_timeout: int
    top_k: int


def _as_int(value: object, default: int) -> int:
    try:
        if value is None:
            return default
        parsed = int(value)
        return parsed
    except (TypeError, ValueError):
        return default


def _read_yaml(path: Path) -> dict:
    if not path.exists():
        return {}
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if isinstance(raw, dict):
        return raw
    return {}


def load_settings(config_path: str | None = None) -> Settings:
    selected = config_path or os.getenv("CHAT_RAG_CONFIG", "config.yaml")
    cfg_path = Path(selected)

    # Allow running from repo root while config lives in chat-rag/config.yaml.
    if not cfg_path.exists() and selected == "config.yaml":
        fallback = Path(__file__).resolve().parents[1] / "config.yaml"
        if fallback.exists():
            cfg_path = fallback

    cfg = _read_yaml(cfg_path)

    paths_cfg = cfg.get("paths", {}) if isinstance(cfg.get("paths"), dict) else {}
    ollama_cfg = cfg.get("ollama", {}) if isinstance(cfg.get("ollama"), dict) else {}
    retrieval_cfg = cfg.get("retrieval", {}) if isinstance(cfg.get("retrieval"), dict) else {}

    db_path = os.getenv("DOCORG_DB_PATH") or str(paths_cfg.get("database", "./docorganizer.db"))
    documents_path = os.getenv("DOCORG_DOCS_PATH") or str(paths_cfg.get("documents", "./documents"))
    ollama_url = os.getenv("OLLAMA_URL") or str(ollama_cfg.get("url", "http://localhost:11434"))
    ollama_model = os.getenv("OLLAMA_MODEL") or str(ollama_cfg.get("model", "mistral:7b-instruct"))
    ollama_timeout = _as_int(os.getenv("OLLAMA_TIMEOUT") or ollama_cfg.get("timeout"), 180)
    top_k = _as_int(os.getenv("TOP_K") or retrieval_cfg.get("top_k"), 8)

    return Settings(
        db_path=db_path,
        documents_path=documents_path,
        ollama_url=ollama_url,
        ollama_model=ollama_model,
        ollama_timeout=ollama_timeout,
        top_k=top_k,
    )
