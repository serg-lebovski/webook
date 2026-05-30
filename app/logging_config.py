"""Структурированное логирование WeBook: отдельные файлы в LOGS_DIR с ротацией.

Логгеры:
  webook.auth    -> auth.log     (вход/выход/регистрация/токены/баны)
  webook.actions -> actions.log  (мутации: кто что менял)
  webook.error   -> errors.log   (необработанные исключения)
  webook.db      -> db.log       (ошибки базы данных)
  webook.access  -> access.log   (HTTP-доступ с таймингом)
"""
import logging
import logging.handlers

from app.config import LOGS_DIR, LOG_LEVEL

_FORMAT = "%(asctime)s %(levelname)s [%(name)s] %(message)s"
_DATEFMT = "%Y-%m-%d %H:%M:%S"
_MAX_BYTES = 5 * 1024 * 1024
_BACKUPS = 5

LOG_FILES = {
    "webook.auth": "auth.log",
    "webook.actions": "actions.log",
    "webook.error": "errors.log",
    "webook.db": "db.log",
    "webook.access": "access.log",
}

# Готовые логгеры для импорта из роутеров/сервисов
auth_log = logging.getLogger("webook.auth")
actions_log = logging.getLogger("webook.actions")
error_log = logging.getLogger("webook.error")
db_log = logging.getLogger("webook.db")
access_log = logging.getLogger("webook.access")


def _file_handler(filename: str) -> logging.Handler:
    h = logging.handlers.RotatingFileHandler(
        LOGS_DIR / filename, maxBytes=_MAX_BYTES, backupCount=_BACKUPS, encoding="utf-8"
    )
    h.setFormatter(logging.Formatter(_FORMAT, _DATEFMT))
    return h


def setup_logging() -> None:
    """Настраивает файловые + консольный обработчики для всех логгеров WeBook."""
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    level = getattr(logging, LOG_LEVEL.upper(), logging.INFO)

    console = logging.StreamHandler()
    console.setFormatter(logging.Formatter(_FORMAT, _DATEFMT))

    for name, filename in LOG_FILES.items():
        lg = logging.getLogger(name)
        lg.setLevel(level)
        lg.propagate = False
        if not lg.handlers:
            lg.addHandler(_file_handler(filename))
            lg.addHandler(console)


def log_action(user, action: str, detail: str = "") -> None:
    """Точечная запись осмысленного действия пользователя в actions.log."""
    uname = getattr(user, "username", "-")
    actions_log.info("%s: %s%s", uname, action, f" — {detail}" if detail else "")
