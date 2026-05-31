import html as _html
import io
import os
import re
import tempfile
import zipfile
from datetime import datetime

from fastapi import APIRouter, Depends, Form, Request, UploadFile, File
from fastapi.responses import HTMLResponse, RedirectResponse, Response, FileResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from starlette.background import BackgroundTask

from app.dependencies import get_db, get_current_user
from app.models.user import User
from app.models.book import Book
from app.models.author import Author
from app.models.shelf import Shelf
from app.models.series import Series
from app.models.share import Share
from app.models.link import Link
from app.models.audiobook import Audiobook
from app.services.auth_service import hash_password, verify_password, create_access_token
from app.config import BOOKS_DIR, COVERS_DIR, LINKS_CONTENT_DIR, AUDIOBOOKS_DIR

router = APIRouter(prefix="/settings")
templates = Jinja2Templates(directory="app/templates")


def _get_stats(db: Session) -> dict:
    books_count = db.query(Book).count()
    authors_count = db.query(Author).count()
    shelves_count = db.query(Shelf).count()
    series_count = db.query(Series).count()

    storage_bytes = sum(
        f.stat().st_size for f in BOOKS_DIR.iterdir() if f.is_file()
    ) if BOOKS_DIR.exists() else 0

    return {
        "books": books_count,
        "authors": authors_count,
        "shelves": shelves_count,
        "series": series_count,
        "storage_mb": round(storage_bytes / 1024 / 1024, 1),
    }


@router.get("", response_class=HTMLResponse)
def settings_page(
    request: Request,
    success: str = "",
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    stats = _get_stats(db)
    return templates.TemplateResponse("settings.html", {
        "request": request,
        "user": user,
        "stats": stats,
        "success": success,
        "error": None,
    })


@router.post("/profile")
def update_profile(
    request: Request,
    current_password: str = Form(...),
    new_username: str = Form(""),
    new_password: str = Form(""),
    new_password2: str = Form(""),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    stats = _get_stats(db)

    def error(msg):
        return templates.TemplateResponse("settings.html", {
            "request": request,
            "user": user,
            "stats": stats,
            "success": "",
            "error": msg,
        }, status_code=400)

    # Проверяем текущий пароль
    if not verify_password(current_password, user.password_hash):
        return error("Неверный текущий пароль")

    changed = False

    # Смена логина
    new_username = new_username.strip()
    if new_username and new_username != user.username:
        if len(new_username) < 3:
            return error("Логин минимум 3 символа")
        exists = db.query(User).filter(
            User.username == new_username, User.id != user.id
        ).first()
        if exists:
            return error("Этот логин уже занят")
        user.username = new_username
        changed = True

    # Смена пароля
    if new_password:
        if len(new_password) < 6:
            return error("Пароль минимум 6 символов")
        if new_password != new_password2:
            return error("Пароли не совпадают")
        user.password_hash = hash_password(new_password)
        changed = True

    if not changed:
        return error("Нет изменений для сохранения")

    db.commit()

    # Перевыпускаем токен (username мог измениться)
    token = create_access_token(user.username)
    response = RedirectResponse("/settings?success=profile", status_code=302)
    response.set_cookie("access_token", token, httponly=True, max_age=60 * 60 * 24 * 7, samesite="lax")
    return response


def _make_icon_png(size: int) -> bytes:
    """Generate a minimal solid blue (#2563eb) PNG for extension icons."""
    import struct, zlib as _zlib
    r, g, b = 37, 99, 235
    scanline = b'\x00' + bytes([r, g, b] * size)
    raw = scanline * size

    def chunk(tag: bytes, data: bytes) -> bytes:
        crc = struct.pack('>I', _zlib.crc32(tag + data) & 0xFFFFFFFF)
        return struct.pack('>I', len(data)) + tag + data + crc

    ihdr = struct.pack('>IIBBBBB', size, size, 8, 2, 0, 0, 0)
    return (
        b'\x89PNG\r\n\x1a\n'
        + chunk(b'IHDR', ihdr)
        + chunk(b'IDAT', _zlib.compress(raw, 9))
        + chunk(b'IEND', b'')
    )


def _safe_filename(s: str, max_len: int = 180) -> str:
    return re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', s).strip()[:max_len]


def _unique_name(name: str, used: set) -> str:
    candidate = name
    i = 2
    while candidate in used:
        base, _, ext = name.rpartition(".")
        candidate = f"{base}_{i}.{ext}" if base else f"{name}_{i}"
        i += 1
    used.add(candidate)
    return candidate


def _article_html(link: Link) -> str:
    title = _html.escape(link.title or "")
    url = _html.escape(link.url or "")
    content = link.content or ""
    paragraphs = "\n".join(
        f"<p>{_html.escape(p.strip()).replace(chr(10), '<br>')}</p>"
        for p in content.split("\n\n") if p.strip()
    )
    return (
        f'<!DOCTYPE html>\n<html lang="ru">\n<head>'
        f'<meta charset="utf-8"><title>{title}</title>\n'
        f'<style>body{{font-family:Georgia,serif;max-width:800px;margin:40px auto;'
        f'line-height:1.7;padding:0 20px}}h1{{font-size:1.4em}}a{{color:#555}}'
        f'p{{margin:0 0 1em}}</style>\n</head>\n<body>\n'
        f'<h1>{title}</h1>\n<p><a href="{url}">{url}</a></p>\n<hr>\n'
        f'{paragraphs}\n</body>\n</html>'
    )


@router.get("/backup")
def backup(
    token: str = "",
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Архив библиотеки пользователя (книги, аудиокниги, обложки, статьи, цитаты)
    + manifest.json для восстановления. Собирается во временный файл и стримится
    через FileResponse — браузер показывает прогресс загрузки."""
    from app.services import archive_service
    fd, tmp_path = tempfile.mkstemp(prefix="webook_backup_", suffix=".zip")
    os.close(fd)
    archive_service.export_user(user, db, tmp_path)
    date_str = datetime.utcnow().strftime("%Y-%m-%d")
    resp = FileResponse(
        tmp_path,
        media_type="application/zip",
        filename=f"webook_{user.username}_{date_str}.zip",
        background=BackgroundTask(lambda: os.path.exists(tmp_path) and os.unlink(tmp_path)),
    )
    if token:
        resp.set_cookie("backup_ready", token, max_age=120, path="/settings", samesite="lax")
    return resp


@router.post("/restore")
async def restore(
    request: Request,
    archive: UploadFile = File(...),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Восстановление библиотеки из своего архива (manifest.json)."""
    from app.services import archive_service
    fd, tmp_path = tempfile.mkstemp(prefix="webook_restore_", suffix=".zip")
    os.close(fd)
    error, result = None, None
    try:
        with open(tmp_path, "wb") as out:
            while True:
                chunk = await archive.read(1024 * 1024)
                if not chunk:
                    break
                out.write(chunk)
        result = archive_service.import_user(user, db, tmp_path)
    except Exception as e:
        db.rollback()
        error = f"Не удалось восстановить архив: {e}"
    finally:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)
    return templates.TemplateResponse("settings.html", {
        "request": request, "user": user, "stats": _get_stats(db),
        "success": "", "error": error, "restore_result": result,
    })


@router.get("/extension/download")
def extension_download(user: User = Depends(get_current_user)):
    from pathlib import Path as _Path
    ext_dir = _Path("extension")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for fname in ("manifest.json", "popup.html", "popup.js"):
            src = ext_dir / fname
            if src.exists():
                zf.writestr(fname, src.read_text(encoding="utf-8"))
        for size in (16, 48, 128):
            zf.writestr(f"icons/icon{size}.png", _make_icon_png(size))
    buf.seek(0)
    return Response(
        content=buf.read(),
        media_type="application/zip",
        headers={"Content-Disposition": 'attachment; filename="webook-extension.zip"'},
    )
