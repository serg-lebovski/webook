"""Обновление всех RSS-подписок. Запуск из host-cron:

    docker exec webook python -m app.scripts.refresh_feeds
"""
from app.database import SessionLocal, init_db
from app.services.feed_service import refresh_all


def main():
    init_db()  # на случай первого запуска — создать таблицы
    db = SessionLocal()
    try:
        total = refresh_all(db)
        print(f"[refresh_feeds] imported {total} new link(s)")
    finally:
        db.close()


if __name__ == "__main__":
    main()
