"""Напоминание об активной серии чтения тем, кто ещё не отмечал сегодня
книгу/статью прочитанной. Запуск из host-cron (раз в день, вечером):

    docker exec webook python -m app.scripts.send_reading_reminders
"""
from datetime import date, timedelta

from app.database import SessionLocal, init_db
from app.models.book import Book
from app.models.link import Link
from app.models.push_subscription import PushSubscription
from app.services import push_service


def _has_active_streak(db, user_id: int) -> tuple[bool, int]:
    """(есть ли риск прервать серию сегодня, длина текущей серии)."""
    since = date.today() - timedelta(days=60)
    days: set = set()
    for (dt,) in db.query(Book.read_at).filter(
        Book.user_id == user_id, Book.deleted_at.is_(None), Book.is_read == True, Book.read_at >= since,
    ).all():
        if dt:
            days.add(dt.date())
    for (dt,) in db.query(Link.read_at).filter(
        Link.user_id == user_id, Link.deleted_at.is_(None), Link.is_read == True, Link.read_at >= since,
    ).all():
        if dt:
            days.add(dt.date())

    today = date.today()
    if today in days:
        return False, 0  # уже читал сегодня — напоминать не о чем

    yesterday = today - timedelta(days=1)
    streak = 0
    cursor = yesterday
    while cursor in days:
        streak += 1
        cursor -= timedelta(days=1)
    return streak > 0, streak


def main():
    init_db()
    db = SessionLocal()
    try:
        user_ids = {row[0] for row in db.query(PushSubscription.user_id).distinct().all()}
        sent = 0
        for uid in user_ids:
            at_risk, streak = _has_active_streak(db, uid)
            if not at_risk:
                continue
            n = push_service.send_to_user(
                db, uid, "Не прервите серию чтения! 🔥",
                f"Серия {streak} дн. подряд — сегодня ещё нет отметки о чтении.",
                url="/dashboard",
            )
            sent += n
        print(f"[send_reading_reminders] sent {sent} notification(s)")
    finally:
        db.close()


if __name__ == "__main__":
    main()
