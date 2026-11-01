"""Статус цикла для пользователя — одно правило на все страницы.

* «прочитан»  — все тома цикла отмечены прочитанными;
* «читаю»     — цикл начат, и последняя активность была не больше ABANDON_DAYS дней назад;
* «заброшен»  — цикл начат, не закончен, и ABANDON_DAYS дней по нему ничего не происходило;
* «не начат»  — ни одного прочитанного тома и ни одной позиции чтения.

Активность = отметка тома прочитанным (Book.read_at) или сохранение позиции
в читалке/приложении (ReadProgress.updated_at при percentage > 0).
Время в БД — naive UTC, сравниваем с datetime.utcnow().
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Iterable, Optional

from sqlalchemy.orm import Session

ABANDON_DAYS = 7
STARTED_PCT = 0.01

LABELS = {
    "done": "цикл прочитан",
    "reading": "читаю",
    "abandoned": "заброшен",
    "new": "не начат",
}


def progress_map(db: Session, user_id: int, book_ids: Iterable[int]) -> dict:
    """{book_id: (percentage, updated_at)} для своих позиций чтения."""
    from app.models.read_progress import ReadProgress
    ids = list({i for i in book_ids if i})
    if not ids:
        return {}
    rows = db.query(ReadProgress).filter(ReadProgress.user_id == user_id, ReadProgress.book_id.in_(ids)).all()
    return {r.book_id: (r.percentage or 0.0, r.updated_at) for r in rows}


def _get(v, name):
    return v.get(name) if isinstance(v, dict) else getattr(v, name, None)


def status(vols: list, progress: dict, now: Optional[datetime] = None) -> dict:
    """vols — книги цикла (ORM Book или dict с id/is_read/read_at).
    → {"code", "label", "last": datetime|None, "days": int|None, "read", "count"}"""
    now = now or datetime.utcnow()
    count = len(vols)
    read = sum(1 for v in vols if _get(v, "is_read"))
    last = None
    started = read > 0
    for v in vols:
        ra = _get(v, "read_at")
        if _get(v, "is_read") and ra and (last is None or ra > last):
            last = ra
        pct, upd = progress.get(_get(v, "id"), (0.0, None))
        if pct and pct > STARTED_PCT:
            started = True
            if upd and (last is None or upd > last):
                last = upd
    days = (now - last).days if last else None
    if count and read == count:
        code = "done"
    elif not started:
        code = "new"
    elif last is None or now - last > timedelta(days=ABANDON_DAYS):
        code = "abandoned"
    else:
        code = "reading"
    return {"code": code, "label": LABELS[code], "last": last, "days": days, "read": read, "count": count}
