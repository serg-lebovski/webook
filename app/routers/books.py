import re
from datetime import datetime
from pathlib import Path
from typing import Optional, List
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Form, Request, UploadFile, File, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse, FileResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import or_
from sqlalchemy.orm import Session, joinedload, selectinload

from app.dependencies import get_db, get_current_user
from app.models.author import Author
from app.models.book import Book
from app.models.audiobook import Audiobook
from app.models.read_progress import ReadProgress
from app.models.series import Series
from app.models.share import Share
from app.models.shelf import Shelf
from app.models.user import User
from app.services.book_service import (
    parse_book_file, save_book_file, save_cover_file,
    delete_file, convert_fb2_to_html,
)
from app.services.tag_service import set_tags_from_string, tags_to_string, get_or_create_tags, parse_tag_names
from app.config import BOOKS_DIR, COVERS_DIR, ALLOWED_BOOK_FORMATS, MAX_BOOK_SIZE

router = APIRouter(prefix="/books")
templates = Jinja2Templates(directory="app/templates")


def _humanbytes(n) -> str:
    try:
        n = float(n or 0)
    except (TypeError, ValueError):
        return "0 B"
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024


templates.env.filters["humanbytes"] = _humanbytes

_READABLE_FORMATS = ("epub", "pdf", "fb2")
_MIME = {"epub": "application/epub+zip", "fb2": "application/x-fictionbook+xml", "pdf": "application/pdf"}
BOOKS_PER_PAGE = 24


def _download_filename(book: Book) -> str:
    """Уникальное, человекочитаемое имя файла для скачивания.

    Включает автора и номер тома в цикле, иначе тома одного цикла с одинаковым
    заголовком получают одинаковое имя файла — и OPDS-читалки (KOReader и др.)
    отдают вместо 2-го/3-го тома уже скачанный первый.
    """
    parts = []
    if book.author and book.author.name:
        parts.append(book.author.name)
    if book.series and book.series.name:
        order = ""
        if book.series_order is not None:
            order = (str(int(book.series_order))
                     if float(book.series_order).is_integer() else str(book.series_order))
        parts.append(f"{book.series.name}{(' ' + order) if order else ''}")
    parts.append(book.title or f"book{book.id}")
    name = " - ".join(p for p in parts if p)
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name).strip(" .") or f"book{book.id}"
    return f"{name}.{book.file_format}"


def _user_books(db: Session, user: User):
    return db.query(Book).filter(Book.user_id == user.id, Book.deleted_at.is_(None))


def _own_book(book_id: int, user: User, db: Session) -> Book:
    book = _user_books(db, user).filter(Book.id == book_id).first()
    if not book:
        raise HTTPException(status_code=404, detail="Книга не найдена")
    return book


DEFAULT_AUTHOR_NAME = "Неизвестный автор"
DEFAULT_SHELF_NAME = "Без полки"


def _get_or_create_author(name: str, db: Session) -> Author:
    name = (name or "").strip() or DEFAULT_AUTHOR_NAME
    a = db.query(Author).filter(Author.name.ilike(name)).first()
    if not a:
        a = Author(name=name)
        db.add(a)
        db.flush()
    return a


def _get_or_create_shelf(name: str, user: User, db: Session) -> Shelf:
    name = (name or "").strip() or DEFAULT_SHELF_NAME
    s = db.query(Shelf).filter(Shelf.user_id == user.id, Shelf.name.ilike(name)).first()
    if not s:
        s = Shelf(name=name, user_id=user.id)
        db.add(s)
        db.flush()
    return s


def _get_or_create_series(name: str, author_id: int, db: Session):
    name = (name or "").strip()
    if not name:
        return None
    s = db.query(Series).filter(Series.author_id == author_id, Series.name.ilike(name)).first()
    if not s:
        s = Series(name=name, author_id=author_id)
        db.add(s)
        db.flush()
    return s


def _accessible_book(book_id: int, user: User, db: Session) -> Book:
    """Returns book if user owns it or has an active internal share. Raises 404 otherwise."""
    book = db.query(Book).filter_by(id=book_id).first()
    if not book:
        raise HTTPException(status_code=404, detail="Книга не найдена")
    if book.user_id == user.id:
        return book
    share = db.query(Share).filter(
        Share.resource_type == "book",
        Share.resource_id == book_id,
        Share.is_public == False,
        Share.shared_with_user_id == user.id,
        Share.expires_at > datetime.utcnow(),
    ).first()
    if not share:
        raise HTTPException(status_code=404, detail="Книга не найдена")
    return book


@router.get("", response_class=HTMLResponse)
def books_list(
    request: Request,
    shelf_id: int = 0,
    author_id: int = 0,
    q: str = "",
    tag: str = "",
    favorite: int = 0,
    reading: int = 0,
    sort: str = "",
    type: str = "all",
    page: int = 1,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    from app.models.tag import Tag

    type_ = type if type in ("all", "book", "audio") else "all"
    book_only_filter = bool(shelf_id or author_id or tag or favorite or reading or sort)
    if type_ == "audio":
        include_books, include_audio = False, True
    elif type_ == "book":
        include_books, include_audio = True, False
    else:
        include_books, include_audio = True, not book_only_filter

    book_items = []
    if include_books:
        query = _user_books(db, user).options(joinedload(Book.author))
        if shelf_id:
            query = query.filter(Book.shelf_id == shelf_id)
        if author_id:
            query = query.filter(Book.author_id == author_id)
        if q:
            query = query.filter(Book.title.ilike(f"%{q}%"))
        if tag:
            query = query.join(Book.tags).filter(Tag.name == tag)
        if favorite:
            query = query.filter(Book.is_favorite == True)
        if reading:
            query = query.filter(Book.in_reading_list == True)
        if sort == "rating":
            query = query.order_by(Book.rating.is_(None), Book.rating.desc(), Book.title)
        else:
            query = query.order_by(Book.title)
        for b in query.all():
            book_items.append({
                "kind": "book", "id": b.id, "title": b.title,
                "author": b.author.name if b.author else "",
                "cover": f"/books/{b.id}/cover" if b.cover_path else None,
                "url": f"/books/{b.id}", "fmt": b.file_format,
                "is_read": b.is_read, "is_favorite": b.is_favorite, "rating": b.rating,
                "in_reading_list": b.in_reading_list, "sort_key": (b.title or "").lower(),
            })

    audio_items = []
    if include_audio:
        aq = db.query(Audiobook).filter(
            Audiobook.user_id == user.id, Audiobook.deleted_at.is_(None)).options(
            selectinload(Audiobook.tracks))
        if q:
            aq = aq.filter(or_(Audiobook.title.ilike(f"%{q}%"), Audiobook.author.ilike(f"%{q}%")))
        for ab in aq.all():
            audio_items.append({
                "kind": "audio", "id": ab.id, "title": ab.title,
                "author": ab.author or "",
                "cover": f"/audiobooks/{ab.id}/cover" if ab.cover_path else None,
                "url": f"/audiobooks/{ab.id}", "chapters": len(ab.tracks),
                "is_finished": ab.is_finished, "sort_key": (ab.title or "").lower(),
            })

    if sort == "rating":
        items = book_items  # уже отсортированы по оценке, аудио исключены
    else:
        items = book_items + audio_items
        items.sort(key=lambda x: x["sort_key"])

    total = len(items)
    total_pages = max(1, (total + BOOKS_PER_PAGE - 1) // BOOKS_PER_PAGE)
    page = min(max(1, page), total_pages)
    items = items[(page - 1) * BOOKS_PER_PAGE: page * BOOKS_PER_PAGE]

    def _filters_qs(**overrides):
        vals = {"shelf_id": shelf_id, "author_id": author_id, "q": q, "tag": tag,
                "favorite": favorite, "reading": reading, "sort": sort,
                "type": type_ if type_ != "all" else ""}
        vals.update(overrides)
        return urlencode({k: v for k, v in vals.items() if v})

    base_qs = _filters_qs()
    fav_qs = _filters_qs(favorite=0 if favorite else 1)
    reading_qs = _filters_qs(reading=0 if reading else 1)
    all_shelf_qs = _filters_qs(shelf_id=0)
    shelves = db.query(Shelf).filter(Shelf.user_id == user.id).order_by(Shelf.sort_order, Shelf.name).all()
    shelf_chips = [{"id": s.id, "name": s.name, "qs": _filters_qs(shelf_id=s.id)} for s in shelves]

    # books shared with this user (shown as a separate section)
    shared_books = []
    if not shelf_id and not author_id and type_ != "audio":
        book_shares = db.query(Share).filter(
            Share.shared_with_user_id == user.id,
            Share.resource_type == "book",
            Share.is_public == False,
            Share.expires_at > datetime.utcnow(),
        ).all()
        if book_shares:
            ids = [s.resource_id for s in book_shares]
            sq = db.query(Book).filter(Book.id.in_(ids))
            if q:
                sq = sq.filter(Book.title.ilike(f"%{q}%"))
            shared_books = sq.order_by(Book.title).all()

    current_url = f"/books{('?' + base_qs) if base_qs else ''}{('&' if base_qs else '?') + 'page=' + str(page) if page > 1 else ''}"

    return templates.TemplateResponse(
        "books/list.html",
        {"request": request, "user": user, "items": items, "shelves": shelves,
         "shelf_chips": shelf_chips, "all_shelf_qs": all_shelf_qs,
         "fav_qs": fav_qs, "reading_qs": reading_qs, "current_url": current_url,
         "q": q, "shelf_id": shelf_id, "tag": tag, "favorite": favorite, "reading": reading, "sort": sort,
         "type": type_, "shared_books": shared_books,
         "page": page, "total_pages": total_pages, "total": total, "base_qs": base_qs},
    )


@router.get("/upload", response_class=HTMLResponse)
def upload_form(
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    shelves = db.query(Shelf).filter(Shelf.user_id == user.id).order_by(Shelf.name).all()
    authors = db.query(Author).order_by(Author.name).all()
    series_all = db.query(Series).order_by(Series.name).all()
    return templates.TemplateResponse(
        "books/upload.html",
        {"request": request, "user": user, "shelves": shelves, "authors": authors,
         "series_all": series_all, "error": None},
    )


@router.post("/bulk-upload")
async def bulk_upload(
    request: Request,
    files: List[UploadFile] = File(...),
    titles: List[str] = Form([]),
    author_names: List[str] = Form([]),
    series_names: List[str] = Form([]),
    series_orders: List[str] = Form([]),
    shelf_names: List[str] = Form([]),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Массовая загрузка книг. Автор/полка/цикл создаются по имени при необходимости.
    Обязательно только название (если пустое — берём из имени файла)."""
    def at(lst, i):
        return lst[i] if i < len(lst) else ""

    created, last_id = 0, None
    for i, f in enumerate(files):
        suffix = Path(f.filename).suffix.lower() if f.filename else ""
        if suffix not in ALLOWED_BOOK_FORMATS:
            continue
        data = await f.read()
        if not data or len(data) > MAX_BOOK_SIZE:
            continue

        title = (at(titles, i) or "").strip() or Path(f.filename).stem
        author = _get_or_create_author(at(author_names, i), db)
        shelf = _get_or_create_shelf(at(shelf_names, i), user, db)
        series = _get_or_create_series(at(series_names, i), author.id, db)
        order_raw = (at(series_orders, i) or "").strip()
        try:
            order = float(order_raw) if order_raw else None
        except ValueError:
            order = None

        meta = parse_book_file(data, suffix)
        cover_path = save_cover_file(meta["cover_data"], ".jpg") if meta.get("cover_data") else None
        file_name = save_book_file(data, suffix)

        book = Book(
            user_id=user.id, title=title, author_id=author.id, shelf_id=shelf.id,
            series_id=series.id if series else None, series_order=order,
            cover_path=cover_path, file_path=file_name, file_format=suffix.lstrip("."),
            file_size=len(data), language=(meta.get("language") or ""),
        )
        db.add(book)
        db.flush()
        last_id, created = book.id, created + 1

    db.commit()
    if created == 0:
        return _upload_error(request, user, db, "Не удалось загрузить ни одной книги. Поддерживаются epub, fb2, pdf.")
    if created == 1 and last_id:
        return RedirectResponse(f"/books/{last_id}", status_code=302)
    return RedirectResponse("/books", status_code=302)


@router.post("/upload")
async def upload_book(
    request: Request,
    title: str = Form(...),
    author_id: int = Form(...),
    shelf_id: int = Form(...),
    series_id: Optional[str] = Form(None),
    series_order: Optional[str] = Form(None),
    description: str = Form(""),
    language: str = Form(""),
    published_year: Optional[str] = Form(None),
    tags: str = Form(""),
    book_file: UploadFile = File(...),
    cover_file: UploadFile = File(None),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    suffix = Path(book_file.filename).suffix.lower() if book_file.filename else ""
    if suffix not in ALLOWED_BOOK_FORMATS:
        return _upload_error(request, user, db, f"Формат {suffix} не поддерживается")

    data = await book_file.read()
    if len(data) > MAX_BOOK_SIZE:
        return _upload_error(request, user, db, "Файл слишком большой (макс 100 МБ)")

    shelf = db.query(Shelf).filter(Shelf.id == shelf_id, Shelf.user_id == user.id).first()
    if not shelf:
        return _upload_error(request, user, db, "Полка не найдена")

    file_name = save_book_file(data, suffix)

    cover_path = None
    if cover_file and cover_file.filename:
        cover_data = await cover_file.read()
        cover_path = save_cover_file(cover_data, Path(cover_file.filename).suffix.lower())

    book = Book(
        user_id=user.id,
        title=title.strip(),
        author_id=author_id,
        shelf_id=shelf_id,
        series_id=int(series_id) if series_id and series_id.strip() else None,
        series_order=float(series_order) if series_order and series_order.strip() else None,
        description=description.strip(),
        cover_path=cover_path,
        file_path=file_name,
        file_format=suffix.lstrip("."),
        file_size=len(data),
        language=language.strip(),
        published_year=int(published_year) if published_year and published_year.strip() else None,
    )
    db.add(book)
    db.commit()
    set_tags_from_string(book, tags, user.id, db)
    db.commit()
    return RedirectResponse(f"/books/{book.id}", status_code=302)


@router.post("/parse-meta")
async def parse_meta(
    file: UploadFile = File(...),
    user: User = Depends(get_current_user),
):
    data = await file.read()
    suffix = Path(file.filename).suffix.lower()
    result = parse_book_file(data, suffix)
    result.pop("cover_data", None)
    return result


# ─────────────────────────── импорт по ISBN ───────────────────────────
# (объявлено до /{book_id}, иначе int-конвертер перехватит "isbn-lookup")

@router.get("/isbn-lookup")
def isbn_lookup(isbn: str = "", user: User = Depends(get_current_user)):
    from app.services.isbn_service import fetch_by_isbn
    meta = fetch_by_isbn(isbn)
    if not meta:
        return JSONResponse({"ok": False, "error": "Книга не найдена по этому ISBN"}, status_code=404)
    return JSONResponse({"ok": True, "meta": meta})


@router.get("/import-csv", response_class=HTMLResponse)
def import_csv_form(request: Request, user: User = Depends(get_current_user)):
    return templates.TemplateResponse("books/import_csv.html", {"request": request, "user": user, "result": None})


@router.post("/import-csv", response_class=HTMLResponse)
async def import_csv(
    request: Request,
    file: UploadFile = File(...),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    from app.services.csv_import_service import import_goodreads_csv
    data = await file.read()
    result = import_goodreads_csv(db, user, data, _get_or_create_author, _get_or_create_shelf)
    return templates.TemplateResponse("books/import_csv.html", {"request": request, "user": user, "result": result})


@router.post("/isbn-import")
def isbn_import(isbn: str = Form(...), shelf: str = Form(""),
                user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    from app.services.isbn_service import fetch_by_isbn, download_cover
    meta = fetch_by_isbn(isbn)
    if not meta or not meta.get("title"):
        return RedirectResponse("/books/upload?isbn_error=1", status_code=302)
    author = _get_or_create_author(meta.get("author") or "", db)
    shelf_obj = _get_or_create_shelf(shelf or "Хочу прочитать", user, db)
    cover_path = None
    cover = download_cover(meta.get("cover_url"))
    if cover:
        cover_path = save_cover_file(cover, ".jpg")
    book = Book(
        title=meta["title"], author_id=author.id, shelf_id=shelf_obj.id, user_id=user.id,
        description=meta.get("description") or "", published_year=meta.get("year"),
        cover_path=cover_path, file_path="", file_format="", file_size=0,
        in_reading_list=True,
    )
    db.add(book)
    db.commit()
    db.refresh(book)
    return RedirectResponse(f"/books/{book.id}", status_code=302)


def _dup_key(title: str) -> str:
    return re.sub(r"\s+", " ", (title or "").strip().lower())


@router.get("/duplicates", response_class=HTMLResponse)
def duplicates_page(request: Request, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Группы книг-дублей: совпадает нормализованное название + автор."""
    books = (
        db.query(Book)
        .options(joinedload(Book.author))
        .filter(Book.user_id == user.id, Book.deleted_at.is_(None))
        .all()
    )
    groups_map: dict = {}
    for b in books:
        key = (_dup_key(b.title), b.author_id)
        groups_map.setdefault(key, []).append(b)

    groups = []
    for (title_key, _author_id), items in groups_map.items():
        if len(items) < 2:
            continue
        # «Лучшую» книгу (с файлом, крупнее, прочитанная) рекомендуем оставить — первой
        items.sort(key=lambda b: (bool(b.file_format), b.file_size or 0, b.is_read, b.id), reverse=True)
        groups.append(items)
    groups.sort(key=lambda g: _dup_key(g[0].title))

    return templates.TemplateResponse("books/duplicates.html", {
        "request": request, "user": user, "groups": groups,
    })


@router.post("/duplicates/resolve")
def duplicates_resolve(
    ids: List[int] = Form(default=[]),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Отправить выбранные книги-дубли в корзину (soft-delete)."""
    if ids:
        now = datetime.utcnow()
        for book in db.query(Book).filter(Book.user_id == user.id, Book.id.in_(ids)).all():
            book.deleted_at = now
        db.commit()
    return RedirectResponse("/books/duplicates", status_code=302)


@router.get("/{book_id}", response_class=HTMLResponse)
def book_detail(book_id: int, request: Request, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    book = _accessible_book(book_id, user, db)
    is_owner = book.user_id == user.id
    share = _get_share(book_id, "book", db) if is_owner else None
    read_progress = db.query(ReadProgress).filter_by(user_id=user.id, book_id=book_id).first()
    return templates.TemplateResponse("books/detail.html", {
        "request": request, "user": user, "book": book, "share": share,
        "is_owner": is_owner, "read_progress": read_progress,
    })


@router.get("/{book_id}/edit", response_class=HTMLResponse)
def edit_book_form(book_id: int, request: Request, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    book = _own_book(book_id, user, db)
    shelves = db.query(Shelf).filter(Shelf.user_id == user.id).all()
    authors = db.query(Author).order_by(Author.name).all()
    series_list = db.query(Series).filter(Series.author_id == book.author_id).all()
    return templates.TemplateResponse(
        "books/edit.html",
        {"request": request, "user": user, "book": book, "shelves": shelves, "authors": authors,
         "series_list": series_list, "tags_str": tags_to_string(book)},
    )


@router.post("/{book_id}/edit")
async def edit_book(
    book_id: int,
    title: str = Form(...),
    author_id: int = Form(...),
    shelf_id: int = Form(...),
    series_id: Optional[str] = Form(None),
    series_order: Optional[str] = Form(None),
    description: str = Form(""),
    language: str = Form(""),
    published_year: Optional[str] = Form(None),
    tags: str = Form(""),
    cover_file: UploadFile = File(None),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    book = _own_book(book_id, user, db)

    book.title = title.strip()
    book.author_id = author_id
    book.shelf_id = shelf_id
    book.series_id = int(series_id) if series_id and series_id.strip() else None
    book.series_order = float(series_order) if series_order and series_order.strip() else None
    book.description = description.strip()
    book.language = language.strip()
    book.published_year = int(published_year) if published_year and published_year.strip() else None

    if cover_file and cover_file.filename:
        cover_data = await cover_file.read()
        delete_file(book.cover_path, COVERS_DIR)
        book.cover_path = save_cover_file(cover_data, Path(cover_file.filename).suffix.lower())

    set_tags_from_string(book, tags, user.id, db)
    db.commit()
    return RedirectResponse(f"/books/{book_id}", status_code=302)


@router.get("/{book_id}/cover")
def book_cover(book_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    book = _accessible_book(book_id, user, db)
    if not book.cover_path:
        raise HTTPException(status_code=404)
    path = COVERS_DIR / book.cover_path
    if not path.exists():
        raise HTTPException(status_code=404)
    return FileResponse(path, media_type="image/jpeg")


@router.get("/{book_id}/download")
def download_book(book_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    book = _accessible_book(book_id, user, db)
    path = BOOKS_DIR / book.file_path
    if not path.exists():
        raise HTTPException(status_code=404)
    return FileResponse(path, media_type=_MIME.get(book.file_format, "application/octet-stream"),
                        filename=_download_filename(book))


@router.get("/{book_id}/serve")
def serve_book_file(book_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """Serve book file inline for in-browser reader (no download disposition)."""
    book = _accessible_book(book_id, user, db)
    path = BOOKS_DIR / book.file_path
    if not path.exists():
        raise HTTPException(status_code=404)
    return FileResponse(path, media_type=_MIME.get(book.file_format, "application/octet-stream"))


@router.get("/{book_id}/read", response_class=HTMLResponse)
def read_book(book_id: int, request: Request, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    book = _accessible_book(book_id, user, db)
    if book.file_format not in _READABLE_FORMATS:
        raise HTTPException(status_code=400, detail=f"Чтение формата {book.file_format.upper()} в браузере не поддерживается")
    content_html = None
    if book.file_format == "fb2":
        path = BOOKS_DIR / book.file_path
        if path.exists():
            content_html = convert_fb2_to_html(path.read_bytes())
    progress_rec = db.query(ReadProgress).filter_by(user_id=user.id, book_id=book_id).first()
    return templates.TemplateResponse("books/read.html", {
        "request": request, "user": user, "book": book, "content_html": content_html,
        "server_progress": progress_rec.progress if progress_rec else None,
        "server_percentage": progress_rec.percentage if progress_rec else None,
    })


@router.post("/{book_id}/progress")
async def save_progress(book_id: int, request: Request, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    _accessible_book(book_id, user, db)
    body = await request.json()
    progress = body.get("progress")
    percentage = body.get("percentage")
    rec = db.query(ReadProgress).filter_by(user_id=user.id, book_id=book_id).first()
    if rec:
        rec.progress = progress
        rec.percentage = float(percentage) if percentage is not None else None
        rec.updated_at = datetime.utcnow()
    else:
        rec = ReadProgress(
            user_id=user.id, book_id=book_id,
            progress=progress,
            percentage=float(percentage) if percentage is not None else None,
        )
        db.add(rec)
    db.commit()
    return JSONResponse({"ok": True})


def _safe_next(next: str, book_id: int) -> str:
    return next if next.startswith("/") and not next.startswith("//") else f"/books/{book_id}"


@router.post("/{book_id}/toggle-read")
def toggle_read_book(book_id: int, next: str = Form(""), user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    book = _own_book(book_id, user, db)
    book.is_read = not book.is_read
    book.read_at = datetime.utcnow() if book.is_read else None
    db.commit()
    return RedirectResponse(_safe_next(next, book_id), status_code=302)


@router.post("/{book_id}/rate")
def rate_book(book_id: int, rating: int = Form(...), next: str = Form(""), user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    book = _own_book(book_id, user, db)
    book.rating = rating if 1 <= rating <= 5 else None  # 0 (или вне диапазона) — сброс
    db.commit()
    return RedirectResponse(_safe_next(next, book_id), status_code=302)


@router.post("/{book_id}/favorite")
def toggle_favorite_book(book_id: int, next: str = Form(""), user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    book = _own_book(book_id, user, db)
    book.is_favorite = not book.is_favorite
    db.commit()
    return RedirectResponse(_safe_next(next, book_id), status_code=302)


@router.post("/{book_id}/reading")
def toggle_reading_list(book_id: int, next: str = Form(""), user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    book = _own_book(book_id, user, db)
    book.in_reading_list = not book.in_reading_list
    db.commit()
    return RedirectResponse(_safe_next(next, book_id), status_code=302)


@router.post("/{book_id}/convert")
def convert_book(book_id: int, target_format: str = Form(...),
                  user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Создаёт новую книгу — результат конвертации формата — рядом с исходной
    (сам файл-источник не трогаем, как и при импорте/дублировании)."""
    from app.services import convert_service

    book = _own_book(book_id, user, db)
    target_format = target_format.lower()
    if target_format not in convert_service.targets_for(book.file_format) or not book.file_path:
        return RedirectResponse(f"/books/{book_id}?convert_error=1", status_code=302)

    src_path = BOOKS_DIR / book.file_path
    if not src_path.exists():
        return RedirectResponse(f"/books/{book_id}?convert_error=1", status_code=302)

    try:
        data = convert_service.convert(
            book.file_format, target_format, src_path.read_bytes(),
            book.title, book.author.name if book.author else "",
        )
        new_path = save_book_file(data, f".{target_format}")
    except Exception:
        return RedirectResponse(f"/books/{book_id}?convert_error=1", status_code=302)

    new_book = Book(
        title=book.title, author_id=book.author_id, series_id=book.series_id,
        series_order=book.series_order, shelf_id=book.shelf_id, user_id=user.id,
        description=book.description, file_path=new_path, file_format=target_format,
        file_size=len(data), language=book.language, published_year=book.published_year,
        in_reading_list=book.in_reading_list,
    )
    db.add(new_book)
    db.commit()
    db.refresh(new_book)
    return RedirectResponse(f"/books/{new_book.id}", status_code=302)


@router.post("/bulk")
def books_bulk(
    action: str = Form(...),
    ids: List[int] = Form(default=[]),
    shelf_id: int = Form(0),
    tag: str = Form(""),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    books = db.query(Book).filter(Book.user_id == user.id, Book.id.in_(ids)).all() if ids else []
    now = datetime.utcnow()
    for book in books:
        if action == "read":
            book.is_read = True
            book.read_at = now
        elif action == "unread":
            book.is_read = False
            book.read_at = None
        elif action == "favorite":
            book.is_favorite = True
        elif action == "unfavorite":
            book.is_favorite = False
        elif action == "move" and shelf_id:
            owns_shelf = db.query(Shelf).filter(Shelf.id == shelf_id, Shelf.user_id == user.id).first()
            if owns_shelf:
                book.shelf_id = shelf_id
        elif action == "tag" and tag.strip():
            names = parse_tag_names(tag)
            new_tags = get_or_create_tags(names, user.id, db)
            have = {t.id for t in book.tags}
            for t in new_tags:
                if t.id not in have:
                    book.tags.append(t)
        elif action == "delete":
            book.deleted_at = now
    db.commit()
    return RedirectResponse("/books", status_code=302)


@router.post("/{book_id}/delete")
def delete_book(book_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    from app.services.trash_service import trash_book
    book = _own_book(book_id, user, db)
    trash_book(book, db)
    db.commit()
    return RedirectResponse("/books", status_code=302)


@router.post("/{book_id}/share")
def share_book(book_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    book = _own_book(book_id, user, db)
    _get_or_create_share(book.id, "book", user.id, db)
    return RedirectResponse(f"/books/{book_id}?shared=1", status_code=302)


@router.post("/{book_id}/unshare")
def unshare_book(book_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    _own_book(book_id, user, db)
    _delete_share(book_id, "book", user.id, db)
    return RedirectResponse(f"/books/{book_id}", status_code=302)


@router.get("/{book_id}/share-user", response_class=HTMLResponse)
def share_book_user_form(
    book_id: int,
    request: Request,
    error: str = "",
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    book = _own_book(book_id, user, db)
    internal_shares = db.query(Share).filter(
        Share.resource_type == "book",
        Share.resource_id == book_id,
        Share.is_public == False,
        Share.expires_at > datetime.utcnow(),
    ).all()
    return templates.TemplateResponse("books/share_user.html", {
        "request": request, "user": user, "book": book,
        "internal_shares": internal_shares, "error": error,
    })


@router.post("/{book_id}/share-with-user")
def share_book_with_user(
    book_id: int,
    username: str = Form(...),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _own_book(book_id, user, db)  # проверка владельца
    target = db.query(User).filter_by(username=username.strip()).first()
    if not target:
        return RedirectResponse(f"/books/{book_id}/share-user?error=not_found", status_code=302)
    if target.id == user.id:
        return RedirectResponse(f"/books/{book_id}/share-user?error=self", status_code=302)
    existing = db.query(Share).filter(
        Share.resource_type == "book",
        Share.resource_id == book_id,
        Share.is_public == False,
        Share.shared_with_user_id == target.id,
        Share.expires_at > datetime.utcnow(),
    ).first()
    if not existing:
        share = Share(
            owner_id=user.id, resource_type="book", resource_id=book_id,
            is_public=False, shared_with_user_id=target.id,
        )
        db.add(share)
        db.commit()
    return RedirectResponse(f"/books/{book_id}", status_code=302)


@router.post("/{book_id}/revoke-user-share")
def revoke_book_user_share(
    book_id: int,
    share_id: int = Form(...),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _own_book(book_id, user, db)
    share = db.query(Share).filter_by(id=share_id, owner_id=user.id).first()
    if share:
        db.delete(share)
        db.commit()
    return RedirectResponse(f"/books/{book_id}/share-user", status_code=302)


# ── helpers ─────────────────────────────────────────────────────────────────

def _upload_error(request, user, db, error):
    shelves = db.query(Shelf).filter(Shelf.user_id == user.id).order_by(Shelf.name).all()
    authors = db.query(Author).order_by(Author.name).all()
    series_all = db.query(Series).order_by(Series.name).all()
    return templates.TemplateResponse(
        "books/upload.html",
        {"request": request, "user": user, "shelves": shelves, "authors": authors,
         "series_all": series_all, "error": error},
        status_code=400,
    )


def _get_share(resource_id: int, resource_type: str, db: Session):
    return db.query(Share).filter(
        Share.resource_type == resource_type,
        Share.resource_id == resource_id,
        Share.is_public == True,
        Share.expires_at > datetime.utcnow(),
    ).first()


def _get_or_create_share(resource_id: int, resource_type: str, owner_id: int, db: Session):
    share = _get_share(resource_id, resource_type, db)
    if not share:
        share = Share(owner_id=owner_id, resource_type=resource_type, resource_id=resource_id, is_public=True)
        db.add(share)
        db.commit()
    return share


def _delete_share(resource_id: int, resource_type: str, owner_id: int, db: Session):
    share = db.query(Share).filter_by(
        resource_type=resource_type, resource_id=resource_id, owner_id=owner_id, is_public=True
    ).first()
    if share:
        db.delete(share)
        db.commit()
