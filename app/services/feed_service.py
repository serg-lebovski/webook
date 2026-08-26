"""Обновление RSS/Atom-подписок: новые записи сохраняются как Link."""
from datetime import datetime

import feedparser
from sqlalchemy.orm import Session

from app.models.feed import Feed
from app.models.link import Link
from app.services.fetch_service import fetch_link_data
from app.config import LINKS_CONTENT_DIR
from app.logging_config import actions_log

MAX_ENTRIES = 50  # максимум новых записей за один проход подписки


def refresh_feed(feed: Feed, db: Session) -> int:
    """Импортирует новые записи подписки. Возвращает число добавленных ссылок."""
    d = feedparser.parse(feed.url, etag=feed.etag or None, modified=feed.last_modified or None)

    if getattr(d, "status", None) == 304:  # не изменилось
        feed.last_checked = datetime.utcnow()
        db.commit()
        return 0

    if getattr(d, "feed", None) and not feed.title:
        feed.title = d.feed.get("title", "") or feed.url

    new_count = 0
    for entry in (d.entries or [])[:MAX_ENTRIES]:
        url = entry.get("link")
        if not url:
            continue
        if db.query(Link).filter_by(user_id=feed.user_id, url=url).first():
            continue

        fetched = {}
        try:
            fetched = fetch_link_data(url)
        except Exception:
            pass
        content = fetched.get("content")

        link = Link(
            title=(entry.get("title") or fetched.get("title") or url)[:500],
            url=url,
            description=(fetched.get("description") or entry.get("summary", "") or "")[:1000],
            folder_id=feed.folder_id,
            user_id=feed.user_id,
            content_fetched_at=datetime.utcnow() if content else None,
        )
        db.add(link)
        db.commit()
        db.refresh(link)

        if content:
            LINKS_CONTENT_DIR.mkdir(parents=True, exist_ok=True)
            (LINKS_CONTENT_DIR / f"{link.id}.txt").write_text(content, encoding="utf-8")
            link.word_count = len(content.split())
            db.commit()
            from app.services.search_service import reindex_link
            reindex_link(db, link)
            db.commit()
        new_count += 1

    feed.etag = getattr(d, "etag", None) or feed.etag
    feed.last_modified = getattr(d, "modified", None) or feed.last_modified
    feed.last_checked = datetime.utcnow()
    db.commit()
    if new_count:
        actions_log.info("feed refresh: %s new from %s (user_id=%s)", new_count, feed.url, feed.user_id)
        try:
            from app.services import push_service
            title = feed.title or feed.url
            body = f"Новых статей: {new_count} — «{title}»"
            push_service.send_to_user(db, feed.user_id, "Новые статьи", body, url="/feeds")
        except Exception:
            pass
    return new_count


def refresh_all(db: Session) -> int:
    total = 0
    for feed in db.query(Feed).all():
        try:
            total += refresh_feed(feed, db)
        except Exception as e:  # одна сломанная подписка не должна останавливать остальные
            actions_log.warning("feed refresh FAILED for %s: %s", feed.url, e)
    return total
