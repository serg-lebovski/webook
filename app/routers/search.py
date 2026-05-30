from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.dependencies import get_db, get_current_user
from app.models.user import User
from app.models.book import Book
from app.models.author import Author
from app.models.series import Series
from app.models.link import Link
from app.models.tag import Tag

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")


@router.get("/search", response_class=HTMLResponse)
def search(
    request: Request,
    q: str = "",
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    q = q.strip()
    books: list = []
    links: list = []

    if q:
        pattern = f"%{q}%"

        # ── Книги: название, описание, автор, цикл, теги ──────────────────
        book_q = (
            db.query(Book)
            .outerjoin(Author, Book.author_id == Author.id)
            .outerjoin(Series, Book.series_id == Series.id)
            .filter(Book.user_id == user.id)
            .filter(
                or_(
                    Book.title.ilike(pattern),
                    Book.description.ilike(pattern),
                    Author.name.ilike(pattern),
                    Series.name.ilike(pattern),
                )
            )
        )
        books = book_q.order_by(Book.title).all()
        book_ids = {b.id for b in books}
        for b in (
            db.query(Book)
            .join(Book.tags)
            .filter(Book.user_id == user.id, Tag.name.ilike(pattern))
            .all()
        ):
            if b.id not in book_ids:
                books.append(b)
                book_ids.add(b.id)
        books.sort(key=lambda b: (b.title or "").lower())

        # ── Статьи: заголовок, описание, URL, теги, полный текст ──────────
        link_q = (
            db.query(Link)
            .filter(Link.user_id == user.id)
            .filter(
                or_(
                    Link.title.ilike(pattern),
                    Link.description.ilike(pattern),
                    Link.url.ilike(pattern),
                )
            )
        )
        links = link_q.order_by(Link.created_at.desc()).all()
        link_ids = {l.id for l in links}
        for l in (
            db.query(Link)
            .join(Link.tags)
            .filter(Link.user_id == user.id, Tag.name.ilike(pattern))
            .all()
        ):
            if l.id not in link_ids:
                links.append(l)
                link_ids.add(l.id)

        # Полнотекстовый поиск по содержимому статей (хранится в файлах)
        ql = q.lower()
        for l in db.query(Link).filter(Link.user_id == user.id).all():
            if l.id in link_ids:
                continue
            content = l.content
            if content and ql in content.lower():
                links.append(l)
                link_ids.add(l.id)

    return templates.TemplateResponse(
        "search.html",
        {"request": request, "user": user, "q": q, "books": books, "links": links},
    )
