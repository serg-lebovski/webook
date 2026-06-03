"""Public share pages — no auth required."""
from datetime import datetime
from fastapi import APIRouter, Depends, Request, HTTPException
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.dependencies import get_db
from app.models.share import Share
from app.models.book import Book
from app.models.link import Link
from app.models.stored_file import StoredFile
from app.config import BOOKS_DIR, COVERS_DIR, FILES_DIR
from app.services.book_service import convert_fb2_to_html

router = APIRouter(prefix="/share")
templates = Jinja2Templates(directory="app/templates")

_MIME = {"epub": "application/epub+zip", "fb2": "application/x-fictionbook+xml", "pdf": "application/pdf"}
_READABLE_FORMATS = ("epub", "pdf", "fb2")


def _get_share_or_404(token: str, db: Session) -> Share:
    share = db.query(Share).filter_by(token=token, is_public=True).first()
    if not share:
        raise HTTPException(status_code=404, detail="Ссылка не найдена или была отозвана")
    if share.is_expired:
        raise HTTPException(status_code=410, detail="Срок действия ссылки истёк")
    return share


def _get_book_or_404(share: Share, db: Session) -> Book:
    if share.resource_type != "book":
        raise HTTPException(status_code=404, detail="Неизвестный тип ресурса")
    book = db.query(Book).filter_by(id=share.resource_id).first()
    if not book:
        raise HTTPException(status_code=404, detail="Книга не найдена")
    return book


@router.get("/{token}", response_class=HTMLResponse)
def shared_view(token: str, request: Request, db: Session = Depends(get_db)):
    share = _get_share_or_404(token, db)

    if share.resource_type == "book":
        return _shared_book(share, request, db)
    elif share.resource_type == "link":
        return _shared_link(share, request, db)
    elif share.resource_type == "file":
        return _shared_file(share, request, db)

    raise HTTPException(status_code=404, detail="Неизвестный тип ресурса")


def _get_file_or_404(share: Share, db: Session) -> StoredFile:
    if share.resource_type != "file":
        raise HTTPException(status_code=404, detail="Неизвестный тип ресурса")
    f = db.query(StoredFile).filter_by(id=share.resource_id).first()
    if not f:
        raise HTTPException(status_code=404, detail="Файл не найден")
    return f


def _shared_file(share: Share, request: Request, db: Session):
    f = _get_file_or_404(share, db)
    return templates.TemplateResponse("share/file.html", {
        "request": request, "user": None, "file": f, "share": share,
    })


@router.get("/{token}/view")
def shared_file_view(token: str, db: Session = Depends(get_db)):
    share = _get_share_or_404(token, db)
    f = _get_file_or_404(share, db)
    path = FILES_DIR / f.stored_name
    if not path.exists():
        raise HTTPException(status_code=404)
    return FileResponse(path, media_type=f.content_type or "application/octet-stream")


@router.get("/{token}/cover")
def shared_book_cover(token: str, db: Session = Depends(get_db)):
    share = _get_share_or_404(token, db)
    book = _get_book_or_404(share, db)
    if not book.cover_path:
        raise HTTPException(status_code=404)
    path = COVERS_DIR / book.cover_path
    if not path.exists():
        raise HTTPException(status_code=404)
    return FileResponse(path, media_type="image/jpeg")


@router.get("/{token}/download")
def shared_book_download(token: str, db: Session = Depends(get_db)):
    share = _get_share_or_404(token, db)
    if share.resource_type == "file":
        f = _get_file_or_404(share, db)
        path = FILES_DIR / f.stored_name
        if not path.exists():
            raise HTTPException(status_code=404)
        from urllib.parse import quote
        return FileResponse(
            path, media_type=f.content_type or "application/octet-stream",
            headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(f.original_name)}"},
        )
    book = _get_book_or_404(share, db)
    path = BOOKS_DIR / book.file_path
    if not path.exists():
        raise HTTPException(status_code=404)
    return FileResponse(
        path,
        media_type=_MIME.get(book.file_format, "application/octet-stream"),
        filename=f"{book.title}.{book.file_format}",
    )


@router.get("/{token}/serve")
def shared_book_serve(token: str, db: Session = Depends(get_db)):
    share = _get_share_or_404(token, db)
    book = _get_book_or_404(share, db)
    path = BOOKS_DIR / book.file_path
    if not path.exists():
        raise HTTPException(status_code=404)
    return FileResponse(path, media_type=_MIME.get(book.file_format, "application/octet-stream"))


@router.get("/{token}/read", response_class=HTMLResponse)
def shared_book_read(token: str, request: Request, db: Session = Depends(get_db)):
    share = _get_share_or_404(token, db)
    book = _get_book_or_404(share, db)
    if book.file_format not in _READABLE_FORMATS:
        raise HTTPException(status_code=400, detail=f"Формат {book.file_format.upper()} не поддерживает чтение в браузере")
    content_html = None
    if book.file_format == "fb2":
        path = BOOKS_DIR / book.file_path
        if path.exists():
            content_html = convert_fb2_to_html(path.read_bytes())
    return templates.TemplateResponse("share/read.html", {
        "request": request,
        "user": None,
        "book": book,
        "token": token,
        "content_html": content_html,
    })


def _shared_book(share: Share, request: Request, db: Session):
    book = db.query(Book).filter_by(id=share.resource_id).first()
    if not book:
        raise HTTPException(status_code=404, detail="Книга не найдена")
    return templates.TemplateResponse("share/book.html", {
        "request": request,
        "user": None,
        "book": book,
        "share": share,
    })


def _shared_link(share: Share, request: Request, db: Session):
    link = db.query(Link).filter_by(id=share.resource_id).first()
    if not link:
        raise HTTPException(status_code=404, detail="Ссылка не найдена")
    return templates.TemplateResponse("share/link.html", {
        "request": request,
        "user": None,
        "link": link,
        "share": share,
    })
