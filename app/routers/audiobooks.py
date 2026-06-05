"""Аудиокниги: загрузка нескольких файлов-глав, плеер, сохранение позиции."""
from datetime import datetime
from pathlib import Path
from typing import List, Optional

from fastapi import APIRouter, Depends, Form, Request, UploadFile, File, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse, FileResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.dependencies import get_db, get_current_user
from app.models.user import User
from app.models.audiobook import Audiobook, AudiobookTrack
from app.services.audiobook_service import (
    new_folder, book_dir, safe_track_name, natural_sort_key, nice_track_title,
    probe_audio, delete_audiobook_folder,
)
from app.services.book_service import save_cover_file
from app.config import COVERS_DIR, ALLOWED_AUDIO_FORMATS, MAX_AUDIO_SIZE

router = APIRouter(prefix="/audiobooks")
templates = Jinja2Templates(directory="app/templates")

_AUDIO_MIME = {
    "mp3": "audio/mpeg", "m4a": "audio/mp4", "m4b": "audio/mp4", "aac": "audio/aac",
    "ogg": "audio/ogg", "oga": "audio/ogg", "opus": "audio/ogg", "flac": "audio/flac",
    "wav": "audio/wav", "webm": "audio/webm",
}


def _own_audiobook(ab_id: int, user: User, db: Session) -> Audiobook:
    ab = db.query(Audiobook).filter_by(id=ab_id, user_id=user.id, deleted_at=None).first()
    if not ab:
        raise HTTPException(status_code=404, detail="Аудиокнига не найдена")
    return ab


def _progress(ab: Audiobook):
    """(процент, подпись статуса) для карточек списка."""
    tracks = ab.tracks
    n = len(tracks)
    if ab.is_finished:
        return 100, "Прослушано"
    if not ab.current_track_id and not (ab.position or 0):
        return 0, "Не начато"
    idx = next((i for i, t in enumerate(tracks) if t.id == ab.current_track_id), 0)
    total = ab.total_duration
    if total > 0:
        elapsed = sum((t.duration or 0) for t in tracks[:idx]) + (ab.position or 0)
        pct = max(0, min(100, round(elapsed / total * 100)))
    else:
        pct = round(idx / n * 100) if n else 0
    return pct, f"Глава {idx + 1} из {n}"


@router.get("", response_class=HTMLResponse)
@router.get("/", response_class=HTMLResponse)
def list_audiobooks(request: Request, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    books = (
        db.query(Audiobook)
        .filter_by(user_id=user.id, deleted_at=None)
        .order_by(Audiobook.created_at.desc())
        .all()
    )
    items = []
    for ab in books:
        pct, label = _progress(ab)
        items.append({"ab": ab, "pct": pct, "label": label, "chapters": len(ab.tracks)})
    return templates.TemplateResponse("audiobooks/list.html", {
        "request": request, "user": user, "items": items,
    })


@router.get("/upload", response_class=HTMLResponse)
def upload_form(request: Request, user: User = Depends(get_current_user)):
    return templates.TemplateResponse("audiobooks/upload.html", {
        "request": request, "user": user, "error": None,
    })


def _upload_error(request: Request, user: User, msg: str):
    return templates.TemplateResponse("audiobooks/upload.html", {
        "request": request, "user": user, "error": msg,
    }, status_code=400)


@router.post("/upload")
async def upload_audiobook(
    request: Request,
    title: str = Form(""),
    author: str = Form(""),
    narrator: str = Form(""),
    files: List[UploadFile] = File(...),
    cover: Optional[UploadFile] = File(None),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    audio = [f for f in files if f.filename and Path(f.filename).suffix.lower() in ALLOWED_AUDIO_FORMATS]
    if not audio:
        return _upload_error(request, user, "Не выбрано ни одного поддерживаемого аудиофайла.")
    audio.sort(key=lambda f: natural_sort_key(f.filename or ""))

    folder = new_folder()
    ab = Audiobook(
        user_id=user.id,
        title=(title.strip() or "Аудиокнига"),
        author=author.strip(),
        narrator=narrator.strip(),
        folder=folder,
    )
    db.add(ab)
    db.flush()  # получить ab.id

    meta_author = meta_title = meta_narrator = ""
    cover_from_tag = None

    try:
        for i, f in enumerate(audio):
            stored = safe_track_name(f.filename)
            dest = book_dir(folder) / stored
            size = 0
            with open(dest, "wb") as out:
                while True:
                    chunk = await f.read(1024 * 1024)
                    if not chunk:
                        break
                    size += len(chunk)
                    if size > MAX_AUDIO_SIZE:
                        out.close()
                        delete_audiobook_folder(folder)
                        db.rollback()
                        return _upload_error(
                            request, user,
                            f"Файл «{f.filename}» больше {MAX_AUDIO_SIZE // (1024*1024)} МБ.")
                    out.write(chunk)

            info = probe_audio(dest)
            track = AudiobookTrack(
                audiobook_id=ab.id,
                filename=stored,
                title=info["title"] or nice_track_title(f.filename),
                order=i,
                duration=info["duration"],
                file_format=Path(f.filename).suffix.lower().lstrip("."),
                file_size=size,
            )
            db.add(track)
            if i == 0:
                meta_author = info["author"]
                meta_narrator = info["narrator"]
                meta_title = info["album"] or info["title"]
                cover_from_tag = info["cover_data"]
    except Exception:
        delete_audiobook_folder(folder)
        db.rollback()
        return _upload_error(request, user, "Не удалось сохранить файлы. Попробуйте ещё раз.")

    if not title.strip():
        ab.title = (meta_title or Path(audio[0].filename).stem or "Аудиокнига").strip()
    if not author.strip() and meta_author:
        ab.author = meta_author
    if not narrator.strip() and meta_narrator:
        ab.narrator = meta_narrator

    # Обложка: загруженная важнее встроенной в теги
    if cover is not None and cover.filename:
        cover_bytes = await cover.read()
        ab.cover_path = save_cover_file(cover_bytes, Path(cover.filename).suffix.lower())
    elif cover_from_tag:
        ab.cover_path = save_cover_file(cover_from_tag, ".jpg")

    db.commit()
    return RedirectResponse(f"/audiobooks/{ab.id}", status_code=302)


@router.get("/{ab_id}", response_class=HTMLResponse)
def player(ab_id: int, request: Request, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    ab = _own_audiobook(ab_id, user, db)
    tracks = [{
        "id": t.id,
        "title": t.title or f"Глава {t.order + 1}",
        "url": f"/audiobooks/{ab.id}/tracks/{t.id}/serve",
        "duration": t.duration or 0,
        "order": t.order,
    } for t in ab.tracks]
    return templates.TemplateResponse("audiobooks/player.html", {
        "request": request, "user": user, "ab": ab, "tracks": tracks,
        "current_track_id": ab.current_track_id, "position": ab.position or 0,
    })


@router.get("/{ab_id}/cover")
def cover(ab_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    ab = _own_audiobook(ab_id, user, db)
    if not ab.cover_path:
        raise HTTPException(status_code=404)
    path = COVERS_DIR / ab.cover_path
    if not path.exists():
        raise HTTPException(status_code=404)
    return FileResponse(path, media_type="image/jpeg")


@router.get("/{ab_id}/tracks/{track_id}/serve")
def serve_track(ab_id: int, track_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    ab = _own_audiobook(ab_id, user, db)
    track = next((t for t in ab.tracks if t.id == track_id), None)
    if not track:
        raise HTTPException(status_code=404)
    path = book_dir(ab.folder) / track.filename
    if not path.exists():
        raise HTTPException(status_code=404)
    # FileResponse поддерживает Range-запросы — нужно для перемотки аудио
    return FileResponse(path, media_type=_AUDIO_MIME.get(track.file_format, "application/octet-stream"))


@router.post("/{ab_id}/progress")
async def save_progress(ab_id: int, request: Request, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    ab = _own_audiobook(ab_id, user, db)
    body = await request.json()
    if body.get("track_id") is not None:
        ab.current_track_id = int(body["track_id"])
    if body.get("position") is not None:
        ab.position = max(0.0, float(body["position"]))
    if body.get("finished") is not None:
        ab.is_finished = bool(body["finished"])
    ab.last_played_at = datetime.utcnow()
    db.commit()
    return JSONResponse({"ok": True})


@router.post("/{ab_id}/finish")
def toggle_finish(ab_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    ab = _own_audiobook(ab_id, user, db)
    ab.is_finished = not ab.is_finished
    db.commit()
    return RedirectResponse(f"/audiobooks/{ab_id}", status_code=302)


@router.post("/{ab_id}/delete")
def delete_audiobook(ab_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    from app.services.trash_service import trash_audiobook
    ab = _own_audiobook(ab_id, user, db)
    trash_audiobook(ab, db)  # в корзину; файлы удалятся при окончательной очистке
    db.commit()
    return RedirectResponse("/audiobooks", status_code=302)
