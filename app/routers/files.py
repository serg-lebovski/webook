"""Файловая шара: загрузка файлов, папки, просмотр медиа в браузере, общий доступ."""
import mimetypes
import os
import re
import tempfile
import uuid
import zipfile
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Optional
from urllib.parse import quote

from fastapi import APIRouter, Depends, Form, Request, UploadFile, File, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse, FileResponse
from fastapi.templating import Jinja2Templates
from starlette.background import BackgroundTask
from sqlalchemy.orm import Session

from app.dependencies import get_db, get_current_user
from app.models.user import User
from app.models.stored_file import StoredFile, FileFolder
from app.models.share import Share
from app.services.settings_service import get_max_file_bytes
from app.config import FILES_DIR

router = APIRouter(prefix="/files")
templates = Jinja2Templates(directory="app/templates")

# Пресеты длительности доступа: подпись -> часы
SHARE_DURATIONS = [
    ("1 час", 1),
    ("6 часов", 6),
    ("1 день", 24),
    ("3 дня", 72),
    ("7 дней", 168),
    ("30 дней", 720),
]
_VALID_HOURS = {h for _, h in SHARE_DURATIONS}


def _expires_from(hours_raw: str) -> datetime:
    try:
        hours = int(hours_raw)
    except (TypeError, ValueError):
        hours = 168
    if hours not in _VALID_HOURS:
        hours = 168
    return datetime.utcnow() + timedelta(hours=hours)


def _own_file(file_id: int, user: User, db: Session) -> StoredFile:
    f = db.query(StoredFile).filter_by(id=file_id, user_id=user.id).first()
    if not f:
        raise HTTPException(status_code=404, detail="Файл не найден")
    return f


def _own_folder(folder_id: int, user: User, db: Session) -> FileFolder:
    fol = db.query(FileFolder).filter_by(id=folder_id, user_id=user.id).first()
    if not fol:
        raise HTTPException(status_code=404, detail="Папка не найдена")
    return fol


# ─────────────────────────── список ───────────────────────────

@router.get("", response_class=HTMLResponse)
@router.get("/", response_class=HTMLResponse)
def files_index(
    request: Request,
    folder: Optional[int] = None,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    current = _own_folder(folder, user, db) if folder else None
    folders = []
    if current is None:
        folders = (
            db.query(FileFolder)
            .filter_by(user_id=user.id)
            .order_by(FileFolder.name)
            .all()
        )
    q = db.query(StoredFile).filter(StoredFile.user_id == user.id)
    q = q.filter(StoredFile.folder_id == (current.id if current else None))
    items = q.order_by(StoredFile.created_at.desc()).all()

    # активные шары по файлам/папкам пользователя — для отметки «в общем доступе»
    now = datetime.utcnow()
    shares = (
        db.query(Share)
        .filter(
            Share.owner_id == user.id,
            Share.resource_type.in_(("file", "file_folder")),
            Share.expires_at > now,
        )
        .all()
    )
    # публичная ссылка активна (is_public) → отметка + токен для копирования
    public_tokens = {
        s.resource_id: s.token
        for s in shares if s.resource_type == "file" and s.is_public
    }
    shared_files = set(public_tokens.keys())
    shared_folders = {s.resource_id for s in shares if s.resource_type == "file_folder"}

    # счётчики файлов в папках (для корневого вида)
    folder_counts = {}
    if folders:
        for fol in folders:
            folder_counts[fol.id] = (
                db.query(StoredFile).filter_by(user_id=user.id, folder_id=fol.id).count()
            )

    all_folders = (
        db.query(FileFolder).filter_by(user_id=user.id).order_by(FileFolder.name).all()
    )

    return templates.TemplateResponse("files/list.html", {
        "request": request, "user": user,
        "current": current, "folders": folders, "items": items,
        "all_folders": all_folders,
        "folder_counts": folder_counts,
        "shared_files": shared_files, "shared_folders": shared_folders,
        "public_tokens": public_tokens,
        "durations": SHARE_DURATIONS,
        "max_mb": get_max_file_bytes(db) // (1024 * 1024),
    })


# ─────────────────────────── папки ───────────────────────────

@router.post("/folders")
def create_folder(name: str = Form(...), user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    name = name.strip()
    if name:
        db.add(FileFolder(user_id=user.id, name=name[:120]))
        db.commit()
    return RedirectResponse("/files", status_code=302)


@router.post("/folders/{folder_id}/rename")
def rename_folder(folder_id: int, name: str = Form(...), user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    fol = _own_folder(folder_id, user, db)
    if name.strip():
        fol.name = name.strip()[:120]
        db.commit()
    return RedirectResponse(f"/files?folder={folder_id}", status_code=302)


@router.post("/folders/{folder_id}/delete")
def delete_folder(folder_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    fol = _own_folder(folder_id, user, db)
    files = db.query(StoredFile).filter_by(user_id=user.id, folder_id=fol.id).all()
    file_ids = [f.id for f in files]
    for f in files:
        (FILES_DIR / f.stored_name).unlink(missing_ok=True)
        db.delete(f)
    # снять связанные шары (на папку и на файлы внутри)
    if file_ids:
        db.query(Share).filter(
            Share.owner_id == user.id, Share.resource_type == "file",
            Share.resource_id.in_(file_ids),
        ).delete(synchronize_session=False)
    db.query(Share).filter(
        Share.owner_id == user.id, Share.resource_type == "file_folder",
        Share.resource_id == fol.id,
    ).delete(synchronize_session=False)
    db.delete(fol)
    db.commit()
    return RedirectResponse("/files", status_code=302)


# ─────────────────────────── загрузка ───────────────────────────

@router.post("/upload")
async def upload_files(
    request: Request,
    files: List[UploadFile] = File(...),
    folder_id: str = Form(""),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    target_folder = None
    if folder_id.strip().isdigit():
        target_folder = _own_folder(int(folder_id), user, db)

    max_bytes = get_max_file_bytes(db)
    for f in files or []:
        if not f or not f.filename:
            continue
        original = Path(f.filename).name
        suffix = Path(original).suffix.lower()
        stored = f"{uuid.uuid4().hex}{suffix}"
        dest = FILES_DIR / stored
        size = 0
        too_big = False
        with open(dest, "wb") as out:
            while True:
                chunk = await f.read(1024 * 1024)
                if not chunk:
                    break
                size += len(chunk)
                if size > max_bytes:
                    too_big = True
                    break
                out.write(chunk)
        if too_big:
            dest.unlink(missing_ok=True)
            continue
        ctype = f.content_type or mimetypes.guess_type(original)[0] or "application/octet-stream"
        db.add(StoredFile(
            user_id=user.id,
            folder_id=target_folder.id if target_folder else None,
            original_name=original, stored_name=stored,
            size=size, content_type=ctype,
        ))
    db.commit()
    dest_url = f"/files?folder={target_folder.id}" if target_folder else "/files"
    return RedirectResponse(dest_url, status_code=302)


# ─────────────────────────── просмотр / скачивание ───────────────────────────

def _serve(f: StoredFile, *, attachment: bool) -> FileResponse:
    path = FILES_DIR / f.stored_name
    if not path.exists():
        raise HTTPException(status_code=404)
    headers = None
    if attachment:
        # quote, чтобы кириллица/спецсимволы в имени не ломали заголовок
        from urllib.parse import quote
        headers = {"Content-Disposition": f"attachment; filename*=UTF-8''{quote(f.original_name)}"}
    return FileResponse(path, media_type=f.content_type or "application/octet-stream",
                        headers=headers)


@router.get("/{file_id}/view")
def view_file(file_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    f = _own_file(file_id, user, db)
    return _serve(f, attachment=False)


@router.get("/{file_id}/download")
def download_file(file_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    f = _own_file(file_id, user, db)
    return _serve(f, attachment=True)


@router.post("/{file_id}/delete")
def delete_stored_file(file_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    f = _own_file(file_id, user, db)
    folder_id = f.folder_id
    (FILES_DIR / f.stored_name).unlink(missing_ok=True)
    db.query(Share).filter(
        Share.owner_id == user.id, Share.resource_type == "file", Share.resource_id == f.id,
    ).delete(synchronize_session=False)
    db.delete(f)
    db.commit()
    back = f"/files?folder={folder_id}" if folder_id else "/files"
    return RedirectResponse(back, status_code=302)


@router.post("/{file_id}/rename")
def rename_file(file_id: int, name: str = Form(...),
                user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    f = _own_file(file_id, user, db)
    new = Path(name.strip()).name
    if new:
        # сохраняем исходное расширение, если пользователь его не указал
        if not Path(new).suffix and f.ext:
            new = f"{new}.{f.ext}"
        f.original_name = new[:255]
        db.commit()
    back = f"/files?folder={f.folder_id}" if f.folder_id else "/files"
    return RedirectResponse(back, status_code=302)


@router.post("/{file_id}/move")
def move_file(file_id: int, target_folder: str = Form(""),
              user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    f = _own_file(file_id, user, db)
    src = f.folder_id
    if target_folder.strip().isdigit():
        f.folder_id = _own_folder(int(target_folder), user, db).id
    else:
        f.folder_id = None
    db.commit()
    back = f"/files?folder={src}" if src else "/files"
    return RedirectResponse(back, status_code=302)


def _zip_filename(name: str) -> str:
    safe = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name).strip() or "files"
    return f"{safe}.zip"


@router.get("/folders/{folder_id}/download-zip")
def download_folder_zip(folder_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    fol = _own_folder(folder_id, user, db)
    files = db.query(StoredFile).filter_by(user_id=user.id, folder_id=fol.id).all()
    if not files:
        raise HTTPException(status_code=404, detail="Папка пуста")
    tmp = tempfile.NamedTemporaryFile(prefix="webook_files_", suffix=".zip", delete=False)
    tmp.close()
    used: dict = {}
    with zipfile.ZipFile(tmp.name, "w") as zf:
        for f in files:
            path = FILES_DIR / f.stored_name
            if not path.exists():
                continue
            arc = Path(f.original_name).name or f.stored_name
            # развести одинаковые имена
            if arc in used:
                used[arc] += 1
                stem, dot, ext = arc.partition(".")
                arc = f"{stem}_{used[arc]}" + (dot + ext if dot else "")
            else:
                used[arc] = 0
            # уже сжатые форматы кладём без повторного сжатия
            ctype = (f.content_type or "")
            compress = zipfile.ZIP_STORED if (f.previewable or "zip" in ctype or "compressed" in ctype) else zipfile.ZIP_DEFLATED
            zf.write(path, arcname=arc, compress_type=compress)
    return FileResponse(
        tmp.name, media_type="application/zip", filename=_zip_filename(fol.name),
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(_zip_filename(fol.name))}"},
        background=BackgroundTask(lambda: os.unlink(tmp.name)),
    )


# ─────────────────────────── публичный доступ (токен) ───────────────────────────

@router.post("/{file_id}/share")
def share_public(file_id: int, hours: str = Form("168"),
                 user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    f = _own_file(file_id, user, db)
    existing = db.query(Share).filter_by(
        owner_id=user.id, resource_type="file", resource_id=f.id, is_public=True,
    ).first()
    if existing:
        existing.expires_at = _expires_from(hours)
    else:
        db.add(Share(owner_id=user.id, resource_type="file", resource_id=f.id,
                     is_public=True, expires_at=_expires_from(hours)))
    db.commit()
    back = f"/files?folder={f.folder_id}" if f.folder_id else "/files"
    return RedirectResponse(back, status_code=302)


@router.post("/{file_id}/unshare")
def unshare_public(file_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    f = _own_file(file_id, user, db)
    db.query(Share).filter_by(
        owner_id=user.id, resource_type="file", resource_id=f.id, is_public=True,
    ).delete(synchronize_session=False)
    db.commit()
    back = f"/files?folder={f.folder_id}" if f.folder_id else "/files"
    return RedirectResponse(back, status_code=302)


@router.get("/{file_id}/link", response_class=HTMLResponse)
def public_link_page(file_id: int, request: Request,
                     user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Маленькая страница с готовой публичной ссылкой (для копирования)."""
    f = _own_file(file_id, user, db)
    share = db.query(Share).filter_by(
        owner_id=user.id, resource_type="file", resource_id=f.id, is_public=True,
    ).first()
    if not share or share.is_expired:
        raise HTTPException(status_code=404, detail="Публичная ссылка не активна")
    return templates.TemplateResponse("files/public_link.html", {
        "request": request, "user": user, "file": f, "share": share,
    })


# ─────────────────────────── доступ для пользователей сервера ───────────────────────────

def _share_user_form(request, user, db, *, kind, obj, error=""):
    rtype = "file" if kind == "file" else "file_folder"
    shares = db.query(Share).filter(
        Share.owner_id == user.id, Share.resource_type == rtype,
        Share.resource_id == obj.id, Share.is_public == False,
        Share.expires_at > datetime.utcnow(),
    ).all()
    return templates.TemplateResponse("files/share_user.html", {
        "request": request, "user": user, "kind": kind, "obj": obj,
        "shares": shares, "error": error, "durations": SHARE_DURATIONS,
    })


def _do_share_with_user(user, db, *, rtype, obj_id, username, hours):
    target = db.query(User).filter_by(username=username.strip()).first()
    if not target:
        return "not_found"
    if target.id == user.id:
        return "self"
    existing = db.query(Share).filter(
        Share.owner_id == user.id, Share.resource_type == rtype, Share.resource_id == obj_id,
        Share.is_public == False, Share.shared_with_user_id == target.id,
        Share.expires_at > datetime.utcnow(),
    ).first()
    if existing:
        existing.expires_at = _expires_from(hours)
    else:
        db.add(Share(owner_id=user.id, resource_type=rtype, resource_id=obj_id,
                     is_public=False, shared_with_user_id=target.id,
                     expires_at=_expires_from(hours)))
    db.commit()
    return None


@router.get("/{file_id}/share-user", response_class=HTMLResponse)
def file_share_user_form(file_id: int, request: Request, error: str = "",
                         user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    f = _own_file(file_id, user, db)
    return _share_user_form(request, user, db, kind="file", obj=f, error=error)


@router.post("/{file_id}/share-with-user")
def file_share_with_user(file_id: int, username: str = Form(...), hours: str = Form("168"),
                         user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    f = _own_file(file_id, user, db)
    err = _do_share_with_user(user, db, rtype="file", obj_id=f.id, username=username, hours=hours)
    if err:
        return RedirectResponse(f"/files/{file_id}/share-user?error={err}", status_code=302)
    return RedirectResponse(f"/files/{file_id}/share-user", status_code=302)


@router.get("/folders/{folder_id}/share-user", response_class=HTMLResponse)
def folder_share_user_form(folder_id: int, request: Request, error: str = "",
                           user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    fol = _own_folder(folder_id, user, db)
    return _share_user_form(request, user, db, kind="folder", obj=fol, error=error)


@router.post("/folders/{folder_id}/share-with-user")
def folder_share_with_user(folder_id: int, username: str = Form(...), hours: str = Form("168"),
                           user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    fol = _own_folder(folder_id, user, db)
    err = _do_share_with_user(user, db, rtype="file_folder", obj_id=fol.id, username=username, hours=hours)
    if err:
        return RedirectResponse(f"/files/folders/{folder_id}/share-user?error={err}", status_code=302)
    return RedirectResponse(f"/files/folders/{folder_id}/share-user", status_code=302)


@router.post("/share/{share_id}/revoke")
def revoke_user_share(share_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    share = db.query(Share).filter_by(id=share_id, owner_id=user.id, is_public=False).first()
    back = "/files"
    if share:
        if share.resource_type == "file":
            back = f"/files/{share.resource_id}/share-user"
        elif share.resource_type == "file_folder":
            back = f"/files/folders/{share.resource_id}/share-user"
        db.delete(share)
        db.commit()
    return RedirectResponse(back, status_code=302)
