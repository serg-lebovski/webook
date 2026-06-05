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
from app.models.audiobook import Audiobook
from app.models.stored_file import StoredFile
from app.models.manga import Manga

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
    audiobooks: list = []
    files: list = []
    manga: list = []

    if q:
        pattern = f"%{q}%"

        # ── Книги: название, описание, автор, цикл, теги ──────────────────
        book_q = (
            db.query(Book)
            .outerjoin(Author, Book.author_id == Author.id)
            .outerjoin(Series, Book.series_id == Series.id)
            .filter(Book.user_id == user.id, Book.deleted_at.is_(None))
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
            .filter(Book.user_id == user.id, Book.deleted_at.is_(None), Tag.name.ilike(pattern))
            .all()
        ):
            if b.id not in book_ids:
                books.append(b)
                book_ids.add(b.id)
        books.sort(key=lambda b: (b.title or "").lower())

        # ── Статьи: заголовок, описание, URL, теги, полный текст ──────────
        link_q = (
            db.query(Link)
            .filter(Link.user_id == user.id, Link.deleted_at.is_(None))
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
            .filter(Link.user_id == user.id, Link.deleted_at.is_(None), Tag.name.ilike(pattern))
            .all()
        ):
            if l.id not in link_ids:
                links.append(l)
                link_ids.add(l.id)

        # Полнотекстовый поиск по содержимому статей (хранится в файлах)
        ql = q.lower()
        for l in db.query(Link).filter(Link.user_id == user.id, Link.deleted_at.is_(None)).all():
            if l.id in link_ids:
                continue
            content = l.content
            if content and ql in content.lower():
                links.append(l)
                link_ids.add(l.id)

        # ── Аудиокниги: название, автор, чтец ──────────────────────────────
        audiobooks = (
            db.query(Audiobook)
            .filter(Audiobook.user_id == user.id)
            .filter(or_(Audiobook.title.ilike(pattern),
                        Audiobook.author.ilike(pattern),
                        Audiobook.narrator.ilike(pattern)))
            .order_by(Audiobook.title)
            .all()
        )

        # ── Манга: название, автор ─────────────────────────────────────────
        manga = (
            db.query(Manga)
            .filter(Manga.user_id == user.id, Manga.deleted_at.is_(None))
            .filter(or_(Manga.title.ilike(pattern), Manga.author.ilike(pattern)))
            .order_by(Manga.title)
            .all()
        )

        # ── Файлы: имя ─────────────────────────────────────────────────────
        files = (
            db.query(StoredFile)
            .filter(StoredFile.user_id == user.id, StoredFile.deleted_at.is_(None))
            .filter(StoredFile.original_name.ilike(pattern))
            .order_by(StoredFile.created_at.desc())
            .all()
        )

    return templates.TemplateResponse(
        "search.html",
        {"request": request, "user": user, "q": q, "books": books, "links": links,
         "audiobooks": audiobooks, "files": files, "manga": manga},
    )
