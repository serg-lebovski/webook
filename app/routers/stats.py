"""Страница статистики чтения (`GET /stats`): итоги за год, серии, топ авторов/тегов,
распределение оценок и форматов. Переиспользует модели Book/Link и паттерн дашборда."""
from datetime import datetime, timedelta, date
from collections import Counter

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.dependencies import get_db, get_current_user
from app.models.user import User
from app.models.book import Book
from app.models.link import Link
from app.models.author import Author

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")

MONTHS_RU = ["Янв", "Фев", "Мар", "Апр", "Май", "Июн",
             "Июл", "Авг", "Сен", "Окт", "Ноя", "Дек"]


def _read_days(db: Session, user_id: int) -> set:
    """Все даты (date) с активностью чтения книг или статей."""
    days: set[date] = set()
    for (dt,) in db.query(Book.read_at).filter(
        Book.user_id == user_id, Book.deleted_at.is_(None),
        Book.is_read == True, Book.read_at.isnot(None),
    ).all():
        if dt:
            days.add(dt.date())
    for (dt,) in db.query(Link.read_at).filter(
        Link.user_id == user_id, Link.deleted_at.is_(None),
        Link.is_read == True, Link.read_at.isnot(None),
    ).all():
        if dt:
            days.add(dt.date())
    return days


def _streaks(days: set) -> tuple[int, int]:
    """(текущая серия, лучшая серия) по множеству дат активности."""
    if not days:
        return 0, 0
    today = datetime.utcnow().date()
    cursor = today
    if cursor not in days and (cursor - timedelta(days=1)) in days:
        cursor -= timedelta(days=1)
    current = 0
    while cursor in days:
        current += 1
        cursor -= timedelta(days=1)

    best = 0
    for d in days:
        if (d - timedelta(days=1)) not in days:   # начало серии
            length = 1
            n = d + timedelta(days=1)
            while n in days:
                length += 1
                n += timedelta(days=1)
            best = max(best, length)
    return current, best


@router.get("/stats", response_class=HTMLResponse)
def stats_page(
    request: Request,
    year: int = 0,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    now = datetime.utcnow()
    sel_year = year or now.year
    y_start = datetime(sel_year, 1, 1)
    y_end = datetime(sel_year + 1, 1, 1)

    def in_year(q, col):
        return q.filter(col >= y_start, col < y_end)

    # Базовые читательские фильтры
    read_books_q = db.query(Book).filter(
        Book.user_id == user.id, Book.deleted_at.is_(None),
        Book.is_read == True, Book.read_at.isnot(None),
    )
    read_links_q = db.query(Link).filter(
        Link.user_id == user.id, Link.deleted_at.is_(None),
        Link.is_read == True, Link.read_at.isnot(None),
    )

    books_year = in_year(read_books_q, Book.read_at).all()
    links_year = in_year(read_links_q, Link.read_at).all()

    # Минуты/слова из статей за год
    words_year = sum(l.word_count or 0 for l in links_year)
    minutes_year = sum(l.reading_minutes for l in links_year)

    # Помесячная активность за выбранный год
    month_books = [0] * 12
    month_links = [0] * 12
    for b in books_year:
        month_books[b.read_at.month - 1] += 1
    for l in links_year:
        month_links[l.read_at.month - 1] += 1

    # Топ авторов за год
    top_authors = (
        in_year(
            db.query(Author.name, func.count(Book.id).label("n"))
            .join(Book, Book.author_id == Author.id)
            .filter(Book.user_id == user.id, Book.deleted_at.is_(None),
                    Book.is_read == True, Book.read_at.isnot(None)),
            Book.read_at,
        )
        .group_by(Author.name)
        .order_by(func.count(Book.id).desc())
        .limit(8)
        .all()
    )

    # Топ тегов за год (книги + статьи)
    tag_counter: Counter = Counter()
    for b in books_year:
        for t in b.tags:
            tag_counter[t.name] += 1
    for l in links_year:
        for t in l.tags:
            tag_counter[t.name] += 1
    top_tags = tag_counter.most_common(12)

    # Форматы прочитанных книг за год
    fmt_counter: Counter = Counter()
    for b in books_year:
        fmt_counter[(b.file_format or "—").upper() or "—"] += 1
    formats = sorted(fmt_counter.items(), key=lambda kv: kv[1], reverse=True)

    # Распределение оценок (по всем оценённым книгам, не только за год)
    rating_rows = (
        db.query(Book.rating, func.count(Book.id))
        .filter(Book.user_id == user.id, Book.deleted_at.is_(None), Book.rating.isnot(None))
        .group_by(Book.rating)
        .all()
    )
    ratings = {r: 0 for r in range(1, 6)}
    for r, n in rating_rows:
        if r in ratings:
            ratings[r] = n
    ratings_max = max(ratings.values()) if any(ratings.values()) else 0

    # Серии чтения
    days = _read_days(db, user.id)
    current_streak, best_streak = _streaks(days)

    # Список годов, по которым есть данные (для переключателя)
    years = sorted({d.year for d in days} | {now.year}, reverse=True)

    return templates.TemplateResponse("stats.html", {
        "request": request,
        "user": user,
        "sel_year": sel_year,
        "years": years,
        "books_count": len(books_year),
        "links_count": len(links_year),
        "words_year": words_year,
        "minutes_year": minutes_year,
        "hours_year": round(minutes_year / 60, 1),
        "month_labels": MONTHS_RU,
        "month_books": month_books,
        "month_links": month_links,
        "top_authors": top_authors,
        "top_tags": top_tags,
        "tags_max": top_tags[0][1] if top_tags else 0,
        "formats": formats,
        "ratings": ratings,
        "ratings_max": ratings_max,
        "current_streak": current_streak,
        "best_streak": best_streak,
        "active_days": len([d for d in days if d.year == sel_year]),
    })
