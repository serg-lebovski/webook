"""Рассылка Telegram-напоминаний. Запуск из host-cron (каждые ~15 минут):

    docker exec webook python -m app.scripts.send_reminders

Для каждого пользователя с привязанным Telegram отправляются события,
наступившие с момента прошлой проверки (`users.tg_last_check`):
  • ссылки общего доступа, входящие в 24-часовое окно до истечения;
  • новые статьи, добавленные с прошлой проверки.
"""
from datetime import datetime, timedelta

from app.database import SessionLocal, init_db
from app.models.user import User
from app.models.link import Link
from app.models.share import Share
from app.services import telegram_service

SHARE_WINDOW = timedelta(hours=24)


def _messages_for(db, user, token, last, now):
    msgs = []

    # шары, вошедшие в 24-часовое окно до истечения с прошлой проверки
    shares = (db.query(Share)
              .filter(((Share.owner_id == user.id) | (Share.shared_with_user_id == user.id)),
                      Share.expires_at > now, Share.expires_at <= now + SHARE_WINDOW)
              .all())
    for s in shares:
        enter = s.expires_at - SHARE_WINDOW  # момент входа в окно
        if last < enter <= now:
            msgs.append(f"⏳ <b>Доступ истекает</b> в течение суток ({s.resource_type})")

    # новые статьи с прошлой проверки
    new_links = (db.query(Link)
                 .filter(Link.user_id == user.id, Link.deleted_at.is_(None),
                         Link.created_at > last, Link.created_at <= now)
                 .count())
    if new_links:
        word = "статья" if new_links == 1 else ("статьи" if new_links < 5 else "статей")
        msgs.append(f"📰 <b>Новых {word}</b>: {new_links}")

    return msgs


def main():
    init_db()
    db = SessionLocal()
    sent = 0
    try:
        token = telegram_service.get_token(db)
        if not token:
            print("[send_reminders] bot token not configured")
            return
        now = datetime.utcnow()
        users = db.query(User).filter(User.telegram_chat_id.isnot(None)).all()
        for user in users:
            last = user.tg_last_check or (now - timedelta(hours=1))
            msgs = _messages_for(db, user, token, last, now)
            for m in msgs:
                if telegram_service.send_message(token, user.telegram_chat_id, m):
                    sent += 1
            user.tg_last_check = now
        db.commit()
        print(f"[send_reminders] sent {sent} message(s) to {len(users)} user(s)")
    finally:
        db.close()


if __name__ == "__main__":
    main()
