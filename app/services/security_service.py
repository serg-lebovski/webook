"""Защита от брутфорса логина: после N неудачных попыток IP блокируется."""
from datetime import datetime, timedelta
from typing import Optional

from fastapi import Request
from sqlalchemy.orm import Session

from app.models.login_attempt import LoginAttempt, IpBan

MAX_FAILED = 5                       # порог неудачных попыток
WINDOW = timedelta(minutes=15)       # окно, в котором они считаются
BAN_DURATION = timedelta(days=2)     # длительность бана


def client_ip(request: Request) -> str:
    """Реальный IP клиента с учётом reverse-proxy (Caddy ставит X-Forwarded-For)."""
    xff = request.headers.get("x-forwarded-for")
    if xff:
        return xff.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def banned_until(ip: str, db: Session) -> Optional[datetime]:
    """Возвращает время окончания бана, если IP заблокирован, иначе None."""
    ban = db.query(IpBan).filter(IpBan.ip == ip).first()
    if not ban:
        return None
    if ban.until > datetime.utcnow():
        return ban.until
    # бан истёк — чистим
    db.delete(ban)
    db.commit()
    return None


def record_failure(ip: str, db: Session) -> bool:
    """Регистрирует неудачную попытку. Возвращает True, если IP только что забанен."""
    db.add(LoginAttempt(ip=ip, success=False))
    db.commit()

    since = datetime.utcnow() - WINDOW
    fails = (
        db.query(LoginAttempt)
        .filter(
            LoginAttempt.ip == ip,
            LoginAttempt.success == False,
            LoginAttempt.created_at >= since,
        )
        .count()
    )
    if fails >= MAX_FAILED:
        until = datetime.utcnow() + BAN_DURATION
        ban = db.query(IpBan).filter(IpBan.ip == ip).first()
        if ban:
            ban.until = until
        else:
            db.add(IpBan(ip=ip, until=until))
        db.commit()
        from app.logging_config import auth_log
        auth_log.warning("IP BANNED ip=%s until=%s after %s failed attempts", ip, until, fails)
        return True
    return False


def record_success(ip: str, db: Session) -> None:
    """Сбрасывает счётчик неудач после успешного входа."""
    db.query(LoginAttempt).filter(
        LoginAttempt.ip == ip, LoginAttempt.success == False
    ).delete()
    db.commit()
