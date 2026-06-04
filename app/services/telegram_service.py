"""Интеграция с Telegram Bot API (исходящие уведомления + привязка чата).

Использует только stdlib (urllib) — без дополнительных зависимостей.
Токен бота хранится в SiteSettings (ключ telegram_bot_token), задаётся админом.
"""
import json
import logging
import urllib.parse
import urllib.request

from sqlalchemy.orm import Session

from app.services.settings_service import get_setting

log = logging.getLogger("webook.actions")

API = "https://api.telegram.org/bot{token}/{method}"
_TIMEOUT = 15


def get_token(db: Session) -> str:
    return (get_setting(db, "telegram_bot_token", "") or "").strip()


def is_configured(db: Session) -> bool:
    return bool(get_token(db))


def _call(token: str, method: str, params: dict) -> dict:
    url = API.format(token=token, method=method)
    data = urllib.parse.urlencode(params).encode("utf-8")
    try:
        with urllib.request.urlopen(url, data=data, timeout=_TIMEOUT) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception as e:  # noqa: BLE001
        log.warning("telegram %s failed: %s", method, e)
        return {"ok": False, "error": str(e)}


def send_message(token: str, chat_id: str, text: str) -> bool:
    if not token or not chat_id:
        return False
    res = _call(token, "sendMessage", {
        "chat_id": chat_id, "text": text,
        "parse_mode": "HTML", "disable_web_page_preview": "true",
    })
    return bool(res.get("ok"))


def get_updates(token: str) -> list:
    res = _call(token, "getUpdates", {"timeout": "0", "limit": "100"})
    return res.get("result", []) if res.get("ok") else []


def find_chat_by_code(token: str, code: str):
    """Ищет среди последних апдейтов сообщение с текстом-кодом → возвращает chat_id (str)."""
    code = (code or "").strip()
    if not code:
        return None
    for upd in reversed(get_updates(token)):
        msg = upd.get("message") or upd.get("edited_message") or {}
        text = (msg.get("text") or "").strip()
        if text == code or text == f"/start {code}":
            chat = msg.get("chat") or {}
            if chat.get("id") is not None:
                return str(chat["id"])
    return None
