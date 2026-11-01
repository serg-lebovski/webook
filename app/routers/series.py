from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.dependencies import get_db, get_current_user
from app.models.series import Series
from app.models.author import Author
from app.models.user import User

router = APIRouter(prefix="/series")
templates = Jinja2Templates(directory="app/templates")
from app.routers.books import _humanbytes  # noqa: E402
templates.env.filters["humanbytes"] = _humanbytes


@router.get("/search")
def search_series(author_id: int = 0, q: str = "", db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    query = db.query(Series)
    if author_id:
        query = query.filter(Series.author_id == author_id)
    if q:
        query = query.filter(Series.name.ilike(f"%{q}%"))
    results = query.limit(10).all()
    return JSONResponse([{"id": s.id, "name": s.name} for s in results])


@router.get("/new", response_class=HTMLResponse)
def new_series_form(request: Request, author_id: int = 0, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    authors = db.query(Author).order_by(Author.name).all()
    selected = db.query(Author).filter(Author.id == author_id).first() if author_id else None
    return templates.TemplateResponse(
        "series/form.html", {"request": request, "user": user, "series": None, "authors": authors, "selected_author": selected, "error": None}
    )


@router.post("/new")
def create_series(
    request: Request,
    name: str = Form(...),
    author_id: int = Form(...),
    description: str = Form(""),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    series = Series(name=name.strip(), author_id=author_id, description=description.strip())
    db.add(series)
    db.commit()
    return RedirectResponse(f"/series/{series.id}", status_code=302)


@router.get("/{series_id}", response_class=HTMLResponse)
def series_detail(series_id: int, request: Request, shelf_id: int = 0,
                  user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    series = db.query(Series).filter(Series.id == series_id).first()
    if not series:
        return RedirectResponse("/authors", status_code=302)
    from app.models.book import Book
    from app.models.shelf import Shelf
    from app.models.read_progress import ReadProgress
    # Только свои книги не из корзины: Series/Author общие для всех пользователей,
    # а series.books вернул бы и чужие тома.
    books = (
        db.query(Book)
        .filter(Book.series_id == series_id, Book.user_id == user.id, Book.deleted_at.is_(None))
        .order_by(Book.series_order.is_(None), Book.series_order, Book.title)
        .all()
    )
    progress = {
        r.book_id: r.percentage or 0
        for r in db.query(ReadProgress).filter(ReadProgress.user_id == user.id,
                                               ReadProgress.book_id.in_([b.id for b in books] or [0]))
    }
    next_book = next((b for b in books if not b.is_read and b.file_format), None)
    from app.services.series_status import status as series_status_of, progress_map
    sstatus = series_status_of(books, progress_map(db, user.id, [b.id for b in books])) if books else None
    tags = []
    for b in books:
        for t in b.tags:
            if t.name not in tags:
                tags.append(t.name)
    description = series.description or next((b.description for b in books if b.description), "")
    shelf = db.query(Shelf).filter(Shelf.id == shelf_id, Shelf.user_id == user.id).first() if shelf_id else None
    other_series = (
        db.query(Series).join(Book, Book.series_id == Series.id)
        .filter(Series.author_id == series.author_id, Series.id != series.id,
                Book.user_id == user.id, Book.deleted_at.is_(None))
        .distinct().order_by(Series.name).limit(10).all()
    )
    return templates.TemplateResponse("series/detail.html", {
        "request": request, "user": user, "series": series, "user_books": books,
        "progress": progress, "next_book": next_book, "tags": tags[:12], "description": description,
        "read_count": sum(1 for b in books if b.is_read),
        "total_size": sum(b.file_size or 0 for b in books),
        "has_files": any(b.file_path for b in books),
        "shelf": shelf, "other_series": other_series, "sstatus": sstatus,
    })


@router.get("/{series_id}/download")
def series_download(series_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Все тома цикла одним ZIP с человекочитаемыми именами (как при скачивании книги)."""
    import os
    import tempfile
    import zipfile
    from fastapi import HTTPException
    from fastapi.responses import FileResponse
    from starlette.background import BackgroundTask
    from app.config import BOOKS_DIR
    from app.models.book import Book
    from app.routers.books import _download_filename

    series = db.query(Series).filter(Series.id == series_id).first()
    books = (
        db.query(Book)
        .filter(Book.series_id == series_id, Book.user_id == user.id, Book.deleted_at.is_(None),
                Book.file_path != "")
        .order_by(Book.series_order.is_(None), Book.series_order, Book.title).all()
    ) if series else []
    files = [(b, BOOKS_DIR / b.file_path) for b in books if b.file_path and (BOOKS_DIR / b.file_path).is_file()]
    if not files:
        raise HTTPException(status_code=404, detail="В цикле нет файлов для скачивания")

    fd, tmp = tempfile.mkstemp(suffix=".zip")
    os.close(fd)
    used = set()
    with zipfile.ZipFile(tmp, "w") as zf:
        for b, path in files:
            name = _download_filename(b)
            base, dot, ext = name.rpartition(".")
            n, candidate = 2, name
            while candidate.lower() in used:
                candidate = f"{base} ({n}).{ext}"
                n += 1
            used.add(candidate.lower())
            method = zipfile.ZIP_DEFLATED if b.file_format == "fb2" else zipfile.ZIP_STORED
            zf.write(path, candidate, compress_type=method)
    import re as _re
    zip_name = _download_safe(f"{series.author.name} - {series.name}") + ".zip"
    return FileResponse(tmp, media_type="application/zip", filename=zip_name,
                        background=BackgroundTask(os.unlink, tmp))


@router.get("/{series_id}/edit", response_class=HTMLResponse)
def edit_series_form(series_id: int, request: Request, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    series = db.query(Series).filter(Series.id == series_id).first()
    if not series:
        return RedirectResponse("/authors", status_code=302)
    authors = db.query(Author).order_by(Author.name).all()
    return templates.TemplateResponse(
        "series/form.html", {"request": request, "user": user, "series": series, "authors": authors, "selected_author": series.author, "error": None}
    )


@router.post("/{series_id}/edit")
def edit_series(
    series_id: int,
    name: str = Form(...),
    author_id: int = Form(...),
    description: str = Form(""),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    series = db.query(Series).filter(Series.id == series_id).first()
    if series:
        series.name = name.strip()
        series.author_id = author_id
        series.description = description.strip()
        db.commit()
    return RedirectResponse(f"/series/{series_id}", status_code=302)


@router.post("/{series_id}/delete")
def delete_series(series_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    series = db.query(Series).filter(Series.id == series_id).first()
    if series:
        author_id = series.author_id
        db.delete(series)
        db.commit()
        return RedirectResponse(f"/authors/{author_id}", status_code=302)
    return RedirectResponse("/authors", status_code=302)


def _download_safe(name: str) -> str:
    bad = '<>:"/' + chr(92) + '|?*'
    out = "".join("_" if (c in bad or ord(c) < 32) else c for c in name)
    return out.strip(" .") or "series"
