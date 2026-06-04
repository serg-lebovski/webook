"""Public share pages — no auth required."""
import hashlib
import hmac
from datetime import datetime
from urllib.parse import quote
from fastapi import APIRouter, Depends, Request, HTTPException, Form
from fastapi.responses import HTMLResponse, FileResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.dependencies import get_db
from app.models.share import Share
from app.models.book import Book
from app.models.link import Link
from app.models.stored_file import StoredFile, FileFolder
from app.config import BOOKS_DIR, COVERS_DIR, FILES_DIR, SECRET_KEY
from app.services.book_service import convert_fb2_to_html
from app.services.auth_service import verify_password

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


# ── защита паролем (cookie-доказательство, подписанное SECRET_KEY) ──
def _pw_proof(share: Share) -> str:
    return hmac.new(SECRET_KEY.encode(),
                    f"{share.token}:{share.password_hash}".encode(), hashlib.sha256).hexdigest()


def _is_unlocked(share: Share, request: Request) -> bool:
    if not share.password_hash:
        return True
    return request.cookies.get(f"sharepw_{share.token}") == _pw_proof(share)


def _require_unlocked(share: Share, request: Request):
    if not _is_unlocked(share, request):
        raise HTTPException(status_code=401, detail="Требуется пароль")


def _count_download(share: Share, db: Session):
    """Проверяет лимит и увеличивает счётчик скачиваний."""
    if share.limit_reached:
        raise HTTPException(status_code=410, detail="Достигнут лимит скачиваний")
    share.download_count = (share.download_count or 0) + 1
    db.commit()


@router.post("/{token}/unlock")
def unlock_share(token: str, password: str = Form(""), db: Session = Depends(get_db)):
    share = _get_share_or_404(token, db)
    if share.password_hash and verify_password(password.strip(), share.password_hash):
        resp = RedirectResponse(f"/share/{token}", status_code=302)
        resp.set_cookie(f"sharepw_{token}", _pw_proof(share), httponly=True, max_age=86400, samesite="lax")
        return resp
    return RedirectResponse(f"/share/{token}?bad=1", status_code=302)


def _get_book_or_404(share: Share, db: Session) -> Book:
    if share.resource_type != "book":
        raise HTTPException(status_code=404, detail="Неизвестный тип ресурса")
    book = db.query(Book).filter_by(id=share.resource_id).first()
    if not book:
        raise HTTPException(status_code=404, detail="Книга не найдена")
    return book


@router.get("/{token}", response_class=HTMLResponse)
def shared_view(token: str, request: Request, bad: int = 0, db: Session = Depends(get_db)):
    share = _get_share_or_404(token, db)

    # пароль (только файлы/папки)
    if share.password_hash and not _is_unlocked(share, request):
        return templates.TemplateResponse("share/unlock.html", {
            "request": request, "token": token, "bad": bad,
        })

    if share.resource_type == "book":
        return _shared_book(share, request, db)
    elif share.resource_type == "link":
        return _shared_link(share, request, db)
    elif share.resource_type == "file":
        return _shared_file(share, request, db)
    elif share.resource_type == "file_folder":
        return _shared_folder(share, request, db)

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


def _shared_folder(share: Share, request: Request, db: Session):
    folder = db.query(FileFolder).filter_by(id=share.resource_id).first()
    if not folder:
        raise HTTPException(status_code=404, detail="Папка не найдена")
    files = (db.query(StoredFile)
             .filter_by(folder_id=folder.id, user_id=share.owner_id, deleted_at=None)
             .order_by(StoredFile.created_at.desc()).all())
    return templates.TemplateResponse("share/folder.html", {
        "request": request, "user": None, "folder": folder, "share": share, "files": files,
    })


def _folder_file(share: Share, file_id: int, db: Session) -> StoredFile:
    f = db.query(StoredFile).filter_by(
        id=file_id, folder_id=share.resource_id, user_id=share.owner_id, deleted_at=None
    ).first()
    if not f:
        raise HTTPException(status_code=404)
    return f


@router.get("/{token}/view")
def shared_file_view(token: str, request: Request, db: Session = Depends(get_db)):
    share = _get_share_or_404(token, db)
    _require_unlocked(share, request)
    f = _get_file_or_404(share, db)
    path = FILES_DIR / f.stored_name
    if not path.exists():
        raise HTTPException(status_code=404)
    return FileResponse(path, media_type=f.content_type or "application/octet-stream")


@router.get("/{token}/f/{file_id}/view")
def shared_folder_file_view(token: str, file_id: int, request: Request, db: Session = Depends(get_db)):
    share = _get_share_or_404(token, db)
    _require_unlocked(share, request)
    f = _folder_file(share, file_id, db)
    path = FILES_DIR / f.stored_name
    if not path.exists():
        raise HTTPException(status_code=404)
    return FileResponse(path, media_type=f.content_type or "application/octet-stream")


@router.get("/{token}/f/{file_id}/download")
def shared_folder_file_download(token: str, file_id: int, request: Request, db: Session = Depends(get_db)):
    share = _get_share_or_404(token, db)
    _require_unlocked(share, request)
    f = _folder_file(share, file_id, db)
    path = FILES_DIR / f.stored_name
    if not path.exists():
        raise HTTPException(status_code=404)
    _count_download(share, db)
    return FileResponse(path, media_type=f.content_type or "application/octet-stream",
                        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(f.original_name)}"})


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
def shared_book_download(token: str, request: Request, db: Session = Depends(get_db)):
    share = _get_share_or_404(token, db)
    if share.resource_type == "file":
        _require_unlocked(share, request)
        f = _get_file_or_404(share, db)
        path = FILES_DIR / f.stored_name
        if not path.exists():
            raise HTTPException(status_code=404)
        _count_download(share, db)
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
