from datetime import datetime, timedelta, date
from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.dependencies import get_db, get_current_user
from app.models.user import User
from app.models.book import Book
from app.models.link import Link, LinkFolder
from app.models.shelf import Shelf
from app.models.share import Share
from app.models.read_progress import ReadProgress

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")

EXPIRING_DAYS = 2  # за сколько дней до истечения напоминать о шарах


def _resource_title(rtype: str, rid: int, db: Session) -> str:
    if rtype == "book":
        obj = db.query(Book).filter(Book.id == rid).first()
        return obj.title if obj else "—"
    if rtype == "link":
        obj = db.query(Link).filter(Link.id == rid).first()
        return obj.title if obj else "—"
    if rtype == "shelf":
        obj = db.query(Shelf).filter(Shelf.id == rid).first()
        return obj.name if obj else "—"
    if rtype == "link_folder":
        obj = db.query(LinkFolder).filter(LinkFolder.id == rid).first()
        return obj.name if obj else "—"
    return "—"


def _expiring_shares(db: Session, user_id: int) -> list:
    """Internal shares (owned or received) expiring within EXPIRING_DAYS."""
    now = datetime.utcnow()
    soon = now + timedelta(days=EXPIRING_DAYS)
    rows = (
        db.query(Share)
        .filter(
            Share.is_public == False,
            Share.expires_at > now,
            Share.expires_at <= soon,
            (Share.owner_id == user_id) | (Share.shared_with_user_id == user_id),
        )
        .order_by(Share.expires_at)
        .all()
    )
    items = []
    for s in rows:
        outgoing = s.owner_id == user_id
        counterpart = s.shared_with.username if outgoing else s.owner.username
        items.append({
            "outgoing": outgoing,
            "title": _resource_title(s.resource_type, s.resource_id, db),
            "counterpart": counterpart,
            "expires_at": s.expires_at,
        })
    return items


def _continue_reading(db: Session, user_id: int, limit: int = 8) -> list:
    """Книги и статьи с незаконченным прогрессом чтения — «продолжить»."""
    items = []

    rows = (
        db.query(ReadProgress, Book)
        .join(Book, Book.id == ReadProgress.book_id)
        .filter(
            ReadProgress.user_id == user_id,
            Book.user_id == user_id,
            Book.deleted_at.is_(None),
            Book.is_read == False,
            ReadProgress.percentage > 0.01,
            ReadProgress.percentage < 0.95,
        )
        .order_by(ReadProgress.updated_at.desc())
        .limit(limit)
        .all()
    )
    for rp, b in rows:
        items.append({
            "kind": "book",
            "title": b.title,
            "url": f"/books/{b.id}/read",
            "cover_url": f"/books/{b.id}/cover" if b.cover_path else None,
            "pct": round((rp.percentage or 0) * 100),
            "updated": rp.updated_at or datetime.min,
        })

    links = (
        db.query(Link)
        .filter(
            Link.user_id == user_id,
            Link.deleted_at.is_(None),
            Link.is_read == False,
            Link.read_progress > 0.01,
            Link.read_progress < 0.95,
        )
        .order_by(Link.created_at.desc())
        .limit(limit)
        .all()
    )
    for l in links:
        items.append({
            "kind": "link",
            "title": l.title,
            "url": f"/links/{l.id}/read",
            "cover_url": None,
            "pct": round((l.read_progress or 0) * 100),
            "updated": l.created_at or datetime.min,
        })

    items.sort(key=lambda x: x["updated"], reverse=True)
    return items[:limit]


def _reading_stats(db: Session, user_id: int, goal: int) -> dict:
    """Статистика за текущий год + серия чтения (подряд идущие дни с активностью)."""
    now = datetime.utcnow()
    year_start = datetime(now.year, 1, 1)

    books_year = db.query(Book).filter(
        Book.user_id == user_id, Book.deleted_at.is_(None), Book.is_read == True, Book.read_at >= year_start,
    ).count()
    links_year = db.query(Link).filter(
        Link.user_id == user_id, Link.deleted_at.is_(None), Link.is_read == True, Link.read_at >= year_start,
    ).count()

    # Собираем все даты с активностью чтения (книги + статьи) за последний год
    since = now - timedelta(days=400)
    days: set[date] = set()
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

    # Текущая серия: считаем подряд идущие дни от сегодня (или вчера) назад
    streak = 0
    cursor = now.date()
    if cursor not in days and (cursor - timedelta(days=1)) in days:
        cursor = cursor - timedelta(days=1)
    while cursor in days:
        streak += 1
        cursor -= timedelta(days=1)

    goal_pct = min(100, round(books_year / goal * 100)) if goal else 0
    return {
        "books_year": books_year,
        "links_year": links_year,
        "year": now.year,
        "goal": goal,
        "goal_pct": goal_pct,
        "goal_left": max(0, goal - books_year) if goal else 0,
        "streak": streak,
    }


def _month_history(db: Session, user_id: int) -> dict:
    """Return last 12 months of reading activity as {labels, books, links}."""
    now = datetime.utcnow()
    labels, books_data, links_data = [], [], []

    for i in range(11, -1, -1):
        # first day of the month i months ago
        month_start = (now.replace(day=1) - timedelta(days=i * 28)).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        # first day of next month
        if month_start.month == 12:
            month_end = month_start.replace(year=month_start.year + 1, month=1)
        else:
            month_end = month_start.replace(month=month_start.month + 1)

        b_count = db.query(Book).filter(
            Book.user_id == user_id,
            Book.deleted_at.is_(None),
            Book.is_read == True,
            Book.read_at >= month_start,
            Book.read_at < month_end,
        ).count()

        l_count = db.query(Link).filter(
            Link.user_id == user_id,
            Link.deleted_at.is_(None),
            Link.is_read == True,
            Link.read_at >= month_start,
            Link.read_at < month_end,
        ).count()

        labels.append(month_start.strftime("%b %Y"))
        books_data.append(b_count)
        links_data.append(l_count)

    return {"labels": labels, "books": books_data, "links": links_data}


@router.get("/dashboard", response_class=HTMLResponse)
def dashboard(
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    books_total = db.query(Book).filter_by(user_id=user.id, deleted_at=None).count()
    books_read  = db.query(Book).filter_by(user_id=user.id, deleted_at=None, is_read=True).count()

    links_total = db.query(Link).filter_by(user_id=user.id, deleted_at=None).count()
    links_read  = db.query(Link).filter_by(user_id=user.id, deleted_at=None, is_read=True).count()

    history = _month_history(db, user.id)
    expiring_shares = _expiring_shares(db, user.id)
    stats = _reading_stats(db, user.id, user.reading_goal or 0)

    from app.services import reco_service
    recommendations = reco_service.recommend(db, user.id, limit=6)
    continue_reading = _continue_reading(db, user.id)

    return templates.TemplateResponse("dashboard.html", {
        "request": request,
        "user": user,
        "continue_reading": continue_reading,
        "recommendations": recommendations,
        "books_total": books_total,
        "books_read": books_read,
        "books_unread": books_total - books_read,
        "links_total": links_total,
        "links_read": links_read,
        "links_unread": links_total - links_read,
        "history": history,
        "expiring_shares": expiring_shares,
        "expiring_days": EXPIRING_DAYS,
        "stats": stats,
    })


@router.post("/dashboard/goal")
def set_reading_goal(
    goal: int = Form(0),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    user.reading_goal = max(0, min(goal, 10000))
    db.commit()
    return RedirectResponse("/dashboard", status_code=302)
