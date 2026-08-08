"""Public share pages — no auth required."""
from fastapi import APIRouter, Depends, Request, HTTPException
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.dependencies import get_db
from app.models.share import Share
from app.models.book import Book
from app.models.link import Link
from app.config import BOOKS_DIR, COVERS_DIR
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
    elif share.resource_type == "manga":
        return _shared_manga(share, request, db)

    raise HTTPException(status_code=404, detail="Неизвестный тип ресурса")


def _get_manga_or_404(share: Share, db: Session):
    from app.models.manga import Manga
    m = db.query(Manga).filter_by(id=share.resource_id).first()
    if not m:
        raise HTTPException(status_code=404, detail="Манга не найдена")
    return m


def _shared_manga(share: Share, request: Request, db: Session):
    m = _get_manga_or_404(share, db)
    return templates.TemplateResponse("share/manga.html", {
        "request": request, "user": None, "manga": m, "share": share,
    })


@router.get("/{token}/read/{chapter_id}", response_class=HTMLResponse)
def shared_manga_reader(token: str, chapter_id: int, request: Request, db: Session = Depends(get_db)):
    from app.routers.manga import reader_ctx
    from app.models.manga import MangaChapter
    share = _get_share_or_404(token, db)
    m = _get_manga_or_404(share, db)
    chapter = db.query(MangaChapter).filter_by(id=chapter_id, manga_id=m.id).first()
    if not chapter:
        raise HTTPException(status_code=404)
    ctx = reader_ctx(m, chapter)
    return templates.TemplateResponse("manga/reader.html", {
        "request": request, "user": None, "manga": m, "chapter": chapter, "title": m.title,
        "start_page": 0, "progress_url": "",
        "back_url": f"/share/{token}",
        "read_base": f"/share/{token}/read/",
        "page_prefix": f"/share/{token}/page/{chapter.id}/",
        **ctx,
    })


@router.get("/{token}/page/{chapter_id}/{n}")
def shared_manga_page(token: str, chapter_id: int, n: int, db: Session = Depends(get_db)):
    from app.routers.manga import serve_page
    from app.models.manga import MangaChapter
    share = _get_share_or_404(token, db)
    m = _get_manga_or_404(share, db)
    chapter = db.query(MangaChapter).filter_by(id=chapter_id, manga_id=m.id).first()
    if not chapter:
        raise HTTPException(status_code=404)
    return serve_page(m, chapter, n)


@router.get("/{token}/cover")
def shared_book_cover(token: str, db: Session = Depends(get_db)):
    share = _get_share_or_404(token, db)
    cover_path = None
    if share.resource_type == "manga":
        cover_path = _get_manga_or_404(share, db).cover_path
    else:
        cover_path = _get_book_or_404(share, db).cover_path
    if not cover_path:
        raise HTTPException(status_code=404)
    path = COVERS_DIR / cover_path
    if not path.exists():
        raise HTTPException(status_code=404)
    return FileResponse(path, media_type="image/jpeg")


@router.get("/{token}/download")
def shared_book_download(token: str, db: Session = Depends(get_db)):
    share = _get_share_or_404(token, db)
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
