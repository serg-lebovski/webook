from typing import Optional
from datetime import datetime

import os

from fastapi import APIRouter, Depends, HTTPException, Header, Request, UploadFile, File
from fastapi.responses import Response
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.orm import Session
from jose import JWTError, jwt

from app.dependencies import get_db
from app.models.user import User
from app.models.link import Link, LinkFolder
from app.models.book import Book
from app.models.shelf import Shelf
from app.models.author import Author
from app.models.read_progress import ReadProgress
from app.services.auth_service import verify_password, create_access_token
from app.services.security_service import client_ip, banned_until, record_failure, record_success
from app.services import book_service
from app.logging_config import auth_log
from app.config import SECRET_KEY, ALGORITHM, BOOKS_DIR, COVERS_DIR

router = APIRouter(prefix="/api")

# Форматы книг, которые приложение умеет озвучивать (извлекаем plain text)
TTS_FORMATS = {"epub", "fb2", "pdf"}


def _get_api_user(
    authorization: Optional[str] = Header(default=None),
    db: Session = Depends(get_db),
) -> User:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Unauthorized")
    token = authorization[7:]
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        username: str = payload.get("sub")
        if not username:
            raise HTTPException(status_code=401, detail="Invalid token")
    except JWTError:
        raise HTTPException(status_code=401, detail="Invalid token")
    user = db.query(User).filter(User.username == username).first()
    if not user:
        raise HTTPException(status_code=401, detail="User not found")
    return user


class TokenRequest(BaseModel):
    username: str
    password: str


class FolderCreate(BaseModel):
    name: str


class LinkCreate(BaseModel):
    url: str
    title: str = ""
    folder_id: Optional[int] = None


@router.post("/token")
def api_token(body: TokenRequest, request: Request, db: Session = Depends(get_db)):
    ip = client_ip(request)
    if banned_until(ip, db):
        auth_log.warning("api token BLOCKED (banned) ip=%s username=%s", ip, body.username)
        raise HTTPException(status_code=429, detail="Слишком много неудачных попыток. Адрес временно заблокирован.")
    user = db.query(User).filter(User.username == body.username).first()
    if not user or not verify_password(body.password, user.password_hash):
        record_failure(ip, db)
        auth_log.warning("api token FAILED ip=%s username=%s", ip, body.username)
        raise HTTPException(status_code=401, detail="Неверный логин или пароль")
    record_success(ip, db)
    auth_log.info("api token OK ip=%s username=%s", ip, user.username)
    token = create_access_token(user.username)
    return {"access_token": token, "username": user.username}


@router.get("/folders")
def list_folders(
    user: User = Depends(_get_api_user),
    db: Session = Depends(get_db),
):
    folders = (
        db.query(LinkFolder)
        .filter(LinkFolder.user_id == user.id)
        .order_by(LinkFolder.sort_order, LinkFolder.name)
        .all()
    )
    return [{"id": f.id, "name": f.name} for f in folders]


@router.post("/folders", status_code=201)
def create_folder(
    body: FolderCreate,
    user: User = Depends(_get_api_user),
    db: Session = Depends(get_db),
):
    name = body.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="Название папки не может быть пустым")
    folder = LinkFolder(name=name, user_id=user.id)
    db.add(folder)
    db.commit()
    db.refresh(folder)
    return {"id": folder.id, "name": folder.name}


@router.post("/links", status_code=201)
def save_link(
    body: LinkCreate,
    user: User = Depends(_get_api_user),
    db: Session = Depends(get_db),
):
    url = body.url.strip()
    if not url:
        raise HTTPException(status_code=400, detail="URL не может быть пустым")
    link = Link(
        url=url,
        title=(body.title.strip() or url)[:500],
        user_id=user.id,
        folder_id=body.folder_id,
    )
    db.add(link)
    db.commit()
    db.refresh(link)
    return {"id": link.id, "title": link.title}


# ---------------------------------------------------------------------------
# Мобильное приложение (Android): список книг/статей + извлечение текста для TTS
# ---------------------------------------------------------------------------

@router.get("/me")
def api_me(user: User = Depends(_get_api_user)):
    return {"id": user.id, "username": user.username, "is_admin": user.is_admin}


@router.get("/shelves")
def api_shelves(
    user: User = Depends(_get_api_user),
    db: Session = Depends(get_db),
):
    """Полки пользователя, в которых есть озвучиваемые книги (как на сайте)."""
    rows = (
        db.query(Shelf.id, Shelf.name, func.count(Book.id))
        .join(Book, Book.shelf_id == Shelf.id)
        .filter(
            Book.user_id == user.id,
            Book.deleted_at.is_(None),
            Book.file_format.in_(TTS_FORMATS),
        )
        .group_by(Shelf.id, Shelf.name)
        .order_by(Shelf.name)
        .all()
    )
    return [{"id": r[0], "name": r[1], "count": r[2]} for r in rows]


@router.get("/authors")
def api_authors(
    user: User = Depends(_get_api_user),
    db: Session = Depends(get_db),
):
    """Авторы пользователя, у которых есть озвучиваемые книги."""
    rows = (
        db.query(Author.id, Author.name, func.count(Book.id))
        .join(Book, Book.author_id == Author.id)
        .filter(
            Book.user_id == user.id,
            Book.deleted_at.is_(None),
            Book.file_format.in_(TTS_FORMATS),
        )
        .group_by(Author.id, Author.name)
        .order_by(Author.name)
        .all()
    )
    return [{"id": r[0], "name": r[1], "count": r[2]} for r in rows]


@router.get("/books")
def api_books(
    q: str = "",
    shelf_id: Optional[int] = None,
    author_id: Optional[int] = None,
    user: User = Depends(_get_api_user),
    db: Session = Depends(get_db),
):
    """Список книг пользователя, доступных для озвучки (epub/fb2/pdf).
    Поддерживает фильтры по полке/автору — для браузинга как на сайте."""
    query = (
        db.query(Book)
        .filter(Book.user_id == user.id, Book.deleted_at.is_(None))
        .filter(Book.file_format.in_(TTS_FORMATS))
    )
    if shelf_id is not None:
        query = query.filter(Book.shelf_id == shelf_id)
    if author_id is not None:
        query = query.filter(Book.author_id == author_id)
    term = q.strip()
    if term:
        query = query.filter(Book.title.ilike(f"%{term}%"))
    books = query.order_by(Book.title).all()
    return [
        {
            "id": b.id,
            "title": b.title,
            "author": b.author.name if b.author else "",
            "format": b.file_format,
            "has_cover": bool(b.cover_path),
            "is_read": b.is_read,
        }
        for b in books
    ]


@router.post("/books/upload")
async def api_upload_book(
    file: UploadFile = File(...),
    user: User = Depends(_get_api_user),
    db: Session = Depends(get_db),
):
    """Загрузка книги (epub/fb2/pdf) с телефона. Автосоздаёт автора/полку."""
    from app.config import ALLOWED_BOOK_FORMATS, MAX_BOOK_SIZE
    from app.routers.books import _get_or_create_author, _get_or_create_shelf

    name = file.filename or "book"
    ext = os.path.splitext(name)[1].lower()
    if ext not in ALLOWED_BOOK_FORMATS:
        raise HTTPException(status_code=400, detail="Поддерживаются только EPUB, FB2, PDF")
    data = await file.read()
    if len(data) > MAX_BOOK_SIZE:
        raise HTTPException(status_code=400, detail="Файл слишком большой")

    meta = book_service.parse_book_file(data, ext)
    title = (meta.get("title") or os.path.splitext(name)[0] or "Без названия").strip()[:500]
    author = _get_or_create_author(meta.get("author") or "", db)
    shelf = _get_or_create_shelf("", user, db)
    file_path = book_service.save_book_file(data, ext)
    cover_path = (
        book_service.save_cover_file(meta.get("cover_data"), ".jpg")
        if meta.get("cover_data") else None
    )
    book = Book(
        title=title,
        user_id=user.id,
        author_id=author.id,
        shelf_id=shelf.id,
        file_path=file_path,
        file_format=ext.lstrip("."),
        file_size=len(data),
        description=meta.get("description") or "",
        language=meta.get("language") or "",
        cover_path=cover_path,
    )
    db.add(book)
    db.commit()
    db.refresh(book)
    return {"id": book.id, "title": book.title, "format": book.file_format}


@router.get("/books/{book_id}/cover")
def api_book_cover(
    book_id: int,
    user: User = Depends(_get_api_user),
    db: Session = Depends(get_db),
):
    book = db.query(Book).filter(
        Book.id == book_id, Book.user_id == user.id, Book.deleted_at.is_(None)
    ).first()
    if not book or not book.cover_path:
        raise HTTPException(status_code=404, detail="No cover")
    path = COVERS_DIR / book.cover_path
    if not path.is_file():
        raise HTTPException(status_code=404, detail="No cover")
    return Response(content=path.read_bytes(), media_type="image/jpeg")


@router.get("/books/{book_id}/text")
def api_book_text(
    book_id: int,
    user: User = Depends(_get_api_user),
    db: Session = Depends(get_db),
):
    """Книга как список абзацев plain text — для озвучки на устройстве."""
    book = db.query(Book).filter(
        Book.id == book_id, Book.user_id == user.id, Book.deleted_at.is_(None)
    ).first()
    if not book:
        raise HTTPException(status_code=404, detail="Book not found")
    if not book.file_path:
        raise HTTPException(status_code=400, detail="Book has no file")
    path = BOOKS_DIR / book.file_path
    if not path.is_file():
        raise HTTPException(status_code=404, detail="File missing")
    paragraphs = book_service.extract_book_text(path.read_bytes(), book.file_format)
    if not paragraphs:
        raise HTTPException(status_code=422, detail="Не удалось извлечь текст книги")
    return {
        "id": book.id,
        "title": book.title,
        "author": book.author.name if book.author else "",
        "format": book.file_format,
        "paragraphs": paragraphs,
    }


@router.get("/articles")
def api_articles(
    q: str = "",
    user: User = Depends(_get_api_user),
    db: Session = Depends(get_db),
):
    """Статьи (сохранённые ссылки) с извлечённым текстом — для озвучки."""
    query = db.query(Link).filter(Link.user_id == user.id, Link.deleted_at.is_(None))
    term = q.strip()
    if term:
        query = query.filter(Link.title.ilike(f"%{term}%"))
    links = query.order_by(Link.created_at.desc()).all()
    out = []
    for l in links:
        if not l.content:
            continue  # без текста озвучивать нечего
        out.append({
            "id": l.id,
            "title": l.title,
            "url": l.url,
            "minutes": l.reading_minutes,
        })
    return out


@router.get("/articles/{link_id}/text")
def api_article_text(
    link_id: int,
    user: User = Depends(_get_api_user),
    db: Session = Depends(get_db),
):
    link = db.query(Link).filter(
        Link.id == link_id, Link.user_id == user.id, Link.deleted_at.is_(None)
    ).first()
    if not link:
        raise HTTPException(status_code=404, detail="Article not found")
    content = link.content or ""
    paragraphs = [" ".join(p.split()) for p in content.split("\n") if p.strip()]
    if not paragraphs:
        raise HTTPException(status_code=422, detail="У статьи нет текста")
    return {
        "id": link.id,
        "title": link.title,
        "author": "",
        "format": "article",
        "paragraphs": paragraphs,
    }


# ---------------------------------------------------------------------------
# Синхронизация позиции чтения/прослушки (доля 0..1) — чтобы продолжить с ПК
# ---------------------------------------------------------------------------

class ProgressBody(BaseModel):
    percentage: float = 0.0


def _clamp01(x: float) -> float:
    return max(0.0, min(1.0, x))


@router.get("/books/{book_id}/progress")
def api_get_book_progress(
    book_id: int,
    user: User = Depends(_get_api_user),
    db: Session = Depends(get_db),
):
    rp = db.query(ReadProgress).filter_by(user_id=user.id, book_id=book_id).first()
    return {"percentage": (rp.percentage or 0.0) if rp else 0.0}


@router.post("/books/{book_id}/progress")
def api_set_book_progress(
    book_id: int,
    body: ProgressBody,
    user: User = Depends(_get_api_user),
    db: Session = Depends(get_db),
):
    book = db.query(Book).filter(
        Book.id == book_id, Book.user_id == user.id, Book.deleted_at.is_(None)
    ).first()
    if not book:
        raise HTTPException(status_code=404, detail="Book not found")
    pct = _clamp01(body.percentage)
    rp = db.query(ReadProgress).filter_by(user_id=user.id, book_id=book_id).first()
    if rp:
        rp.percentage = pct
        rp.updated_at = datetime.utcnow()
    else:
        # progress (CFI/scrollTop) не трогаем — это поле веб-ридера
        db.add(ReadProgress(user_id=user.id, book_id=book_id, percentage=pct))
    db.commit()
    return {"ok": True, "percentage": pct}


@router.get("/articles/{link_id}/progress")
def api_get_article_progress(
    link_id: int,
    user: User = Depends(_get_api_user),
    db: Session = Depends(get_db),
):
    link = db.query(Link).filter(
        Link.id == link_id, Link.user_id == user.id, Link.deleted_at.is_(None)
    ).first()
    if not link:
        raise HTTPException(status_code=404, detail="Article not found")
    return {"percentage": link.read_progress or 0.0}


@router.post("/articles/{link_id}/progress")
def api_set_article_progress(
    link_id: int,
    body: ProgressBody,
    user: User = Depends(_get_api_user),
    db: Session = Depends(get_db),
):
    link = db.query(Link).filter(
        Link.id == link_id, Link.user_id == user.id, Link.deleted_at.is_(None)
    ).first()
    if not link:
        raise HTTPException(status_code=404, detail="Article not found")
    link.read_progress = _clamp01(body.percentage)
    db.commit()
    return {"ok": True, "percentage": link.read_progress}
