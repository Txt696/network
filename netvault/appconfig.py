"""Настройки самого приложения (какое хранилище открывать), не данные vault."""

import json
import os
from pathlib import Path

APP_DIR = Path.home() / ".netvault"
APP_CONFIG = APP_DIR / "app.json"
ENV_VAULT = "NETVAULT_PATH"
ENV_PASSWORD = "NETVAULT_PASSWORD"  # для автоматизации; в GUI не используется

DEFAULTS = {"last_vault": "", "recent": []}


def default_vault_path():
    """Куда класть хранилище, если пользователь не выбрал путь."""
    env = os.environ.get(ENV_VAULT)
    if env:
        return Path(env).expanduser()
    documents = Path.home() / "Documents"
    base = documents if documents.is_dir() else Path.home()
    return base / "NetVault"


def load():
    try:
        return dict(DEFAULTS, **json.loads(APP_CONFIG.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        return dict(DEFAULTS)


def save(config):
    APP_DIR.mkdir(parents=True, exist_ok=True)
    APP_CONFIG.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")
    return config


def remember_vault(path):
    """Запомнить последнее открытое хранилище и добавить его в список недавних."""
    path = str(Path(path).expanduser().resolve())
    config = load()
    recent = [p for p in config.get("recent", []) if p != path]
    recent.insert(0, path)
    config["recent"] = recent[:8]
    config["last_vault"] = path
    return save(config)


def resolve_vault_path(explicit=None):
    """Путь к хранилищу: аргумент → переменная окружения → последнее → по умолчанию."""
    if explicit:
        return Path(explicit).expanduser()
    if os.environ.get(ENV_VAULT):
        return Path(os.environ[ENV_VAULT]).expanduser()
    last = load().get("last_vault")
    if last and Path(last).exists():
        return Path(last)
    return default_vault_path()
