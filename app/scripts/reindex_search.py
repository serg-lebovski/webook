"""Разовая переиндексация полнотекстового поиска по статьям (PostgreSQL).

Заполняет `links.content_tsv` для всех существующих статей. Запуск на сервере:
    docker exec webook python -m app.scripts.reindex_search

На SQLite ничего не делает (FTS недоступен).
"""
from app.database import SessionLocal
from app.models import (  # noqa: F401 — регистрация всех мапперов SQLAlchemy
    user, shelf, author, series, book, link, site_settings, share,
    read_progress, login_attempt, tag, highlight, feed, audiobook, series_tier,
    stored_file, manga, collection,
)
from app.models.link import Link
from app.services import search_service


def main() -> None:
    db = SessionLocal()
    try:
        if not search_service.is_postgres(db):
            print("Не PostgreSQL — переиндексация не требуется.")
            return
        links = db.query(Link).filter(Link.deleted_at.is_(None)).all()
        total = len(links)
        done = 0
        for link in links:
            search_service.reindex_link(db, link)
            done += 1
            if done % 100 == 0:
                db.commit()
                print(f"  {done}/{total}…")
        db.commit()
        print(f"Готово: переиндексировано {done} статей.")
    finally:
        db.close()


if __name__ == "__main__":
    main()
