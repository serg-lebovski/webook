"""Обновление приложения из GitHub-репозитория.

Кнопка «Проверить обновления» (в /admin и /settings) не деплоит сама — она
пишет файл-триггер в смонтированный каталог UPDATE_DIR. Host-watcher на
сервере (вне этого репозитория — systemd unit `webook-update.service`,
`/opt/webook/update-watcher.sh`) видит триггер, проверяет `origin/master`
(`git fetch` + сравнение commit hash) и, если есть новые коммиты, выполняет
`git pull` + пересборку контейнера, публикуя промежуточные этапы и итоговую
версию обратно в тот же каталог (`version.json` / `status.json` /
`available.json`). Тот же watcher раз в час сам проверяет обновления без
участия пользователя.
"""
import json
from datetime import datetime

from app.config import UPDATE_DIR

# Состояния, в которых процесс ещё выполняется (для отображения спиннера).
IN_PROGRESS_STATES = {"queued", "checking", "updating", "pulling", "building", "restarting"}

STAGE_LABELS = {
    "queued": "В очереди…",
    "checking": "Проверка обновлений…",
    "updating": "Запуск обновления…",
    "pulling": "Скачивание кода…",
    "building": "Сборка образа…",
    "restarting": "Перезапуск контейнера…",
    "done": "Готово",
    "error": "Ошибка обновления",
    "idle": "Нет обновлений",
}


def _read_json(path):
    try:
        if path.is_file():
            return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        pass
    return None


def read_state() -> dict:
    """Текущая версия, статус последнего обновления и доступность новой версии."""
    return {
        "version": _read_json(UPDATE_DIR / "version.json"),
        "status": _read_json(UPDATE_DIR / "status.json"),
        "available": _read_json(UPDATE_DIR / "available.json"),
        "queued": (UPDATE_DIR / "trigger").exists() or (UPDATE_DIR / "check_trigger").exists(),
    }


def trigger_check() -> None:
    """Запросить проверку обновлений. Если watcher найдёт новые коммиты — обновится сам."""
    UPDATE_DIR.mkdir(parents=True, exist_ok=True)
    (UPDATE_DIR / "check_trigger").write_text(datetime.utcnow().isoformat(), encoding="utf-8")
    (UPDATE_DIR / "status.json").write_text(
        json.dumps({"state": "checking", "at": datetime.utcnow().isoformat()}),
        encoding="utf-8")
