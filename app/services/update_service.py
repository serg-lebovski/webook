"""Обновление приложения из GitHub-репозитория.

Кнопка «Обновить» (в /admin и /settings) не деплоит сама — она пишет
файл-триггер в смонтированный каталог UPDATE_DIR. Host-watcher на сервере
(вне этого репозитория) видит триггер, выполняет git pull + docker compose
up --build и пишет version.json / status.json обратно в тот же каталог.
"""
import json
from datetime import datetime

from app.config import UPDATE_DIR


def read_state() -> dict:
    """Текущая версия (version.json) и статус последнего обновления (status.json)."""
    info = {"version": None, "status": None, "queued": False}
    try:
        vf = UPDATE_DIR / "version.json"
        if vf.is_file():
            info["version"] = json.loads(vf.read_text(encoding="utf-8"))
    except Exception:
        pass
    try:
        sf = UPDATE_DIR / "status.json"
        if sf.is_file():
            info["status"] = json.loads(sf.read_text(encoding="utf-8"))
    except Exception:
        pass
    info["queued"] = (UPDATE_DIR / "trigger").exists()
    return info


def trigger() -> None:
    """Поставить обновление в очередь: пишем файл-триггер для host-watcher."""
    UPDATE_DIR.mkdir(parents=True, exist_ok=True)
    (UPDATE_DIR / "trigger").write_text(datetime.utcnow().isoformat(), encoding="utf-8")
    (UPDATE_DIR / "status.json").write_text(
        json.dumps({"state": "queued", "at": datetime.utcnow().isoformat()}),
        encoding="utf-8")
