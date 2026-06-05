"""Манга: загрузка глав (изображения или CBZ/ZIP) и веб-читалка."""
from datetime import datetime
from pathlib import Path
from typing import List, Optional

from fastapi import APIRouter, Depends, Form, Request, UploadFile, File, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse, FileResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.dependencies import get_db, get_current_user
from app.models.user import User
from app.models.manga import Manga, MangaChapter
from app.services import manga_service
from app.services.book_service import save_cover_file, delete_file
from app.config import COVERS_DIR, MAX_MANGA_ARCHIVE_SIZE

router = APIRouter(prefix="/manga")
templates = Jinja2Templates(directory="app/templates")


def _own_manga(manga_id: int, user: User, db: Session) -> Manga:
    m = db.query(Manga).filter_by(id=manga_id, user_id=user.id, deleted_at=None).first()
    if not m:
        raise HTTPException(status_code=404, detail="Манга не найдена")
    return m


def _own_chapter(m: Manga, chapter_id: int, db: Session) -> MangaChapter:
    c = db.query(MangaChapter).filter_by(id=chapter_id, manga_id=m.id).first()
    if not c:
        raise HTTPException(status_code=404, detail="Глава не найдена")
    return c


async def _read_uploads(files: List[UploadFile]):
    """Читает загруженные файлы в (name, bytes), отбрасывая слишком большие."""
    items = []
    for f in files or []:
        if not f or not f.filename:
            continue
        data = await f.read()
        if not data or len(data) > MAX_MANGA_ARCHIVE_SIZE:
            continue
        items.append((f.filename, data))
    return items


# ─────────────────────────── список ───────────────────────────

@router.get("", response_class=HTMLResponse)
@router.get("/", response_class=HTMLResponse)
def manga_list(request: Request, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    items = (db.query(Manga)
             .filter(Manga.user_id == user.id, Manga.deleted_at.is_(None))
             .order_by(Manga.last_read_at.is_(None), Manga.last_read_at.desc(), Manga.title)
             .all())
    return templates.TemplateResponse("manga/list.html", {
        "request": request, "user": user, "items": items,
    })


# ─────────────────────────── загрузка ───────────────────────────

@router.get("/upload", response_class=HTMLResponse)
def upload_form(request: Request, user: User = Depends(get_current_user)):
    return templates.TemplateResponse("manga/upload.html", {"request": request, "user": user})


@router.post("/upload")
async def upload_manga(
    title: str = Form(...),
    author: str = Form(""),
    description: str = Form(""),
    chapter_title: str = Form(""),
    cover: Optional[UploadFile] = File(None),
    files: List[UploadFile] = File([]),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if not title.strip():
        return RedirectResponse("/manga/upload", status_code=302)

    folder = manga_service.new_folder()
    m = Manga(user_id=user.id, title=title.strip(), author=author.strip(),
              description=description.strip(), folder=folder)
    db.add(m)
    db.flush()

    images = manga_service.expand_uploads(await _read_uploads(files))
    if images:
        ch_folder, count, first = manga_service.save_chapter(folder, images)
        db.add(MangaChapter(manga_id=m.id, title=chapter_title.strip(),
                            order=1, folder=ch_folder, page_count=count))
        # обложка: загруженная важнее первой страницы
        if cover is not None and cover.filename:
            data = await cover.read()
            if data:
                m.cover_path = save_cover_file(data, Path(cover.filename).suffix.lower() or ".jpg")
        elif first:
            first_bytes = (manga_service.chapter_dir(folder, ch_folder) / first).read_bytes()
            m.cover_path = save_cover_file(first_bytes, Path(first).suffix.lower() or ".jpg")
    db.commit()
    return RedirectResponse(f"/manga/{m.id}", status_code=302)


@router.post("/{manga_id}/chapters")
async def add_chapter(
    manga_id: int,
    chapter_title: str = Form(""),
    files: List[UploadFile] = File([]),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    m = _own_manga(manga_id, user, db)
    images = manga_service.expand_uploads(await _read_uploads(files))
    if images:
        next_order = (max((c.order for c in m.chapters), default=0)) + 1
        ch_folder, count, first = manga_service.save_chapter(m.folder, images)
        db.add(MangaChapter(manga_id=m.id, title=chapter_title.strip(),
                            order=next_order, folder=ch_folder, page_count=count))
        if not m.cover_path and first:
            fb = (manga_service.chapter_dir(m.folder, ch_folder) / first).read_bytes()
            m.cover_path = save_cover_file(fb, Path(first).suffix.lower() or ".jpg")
        db.commit()
    return RedirectResponse(f"/manga/{manga_id}", status_code=302)


# ─────────────────────────── просмотр / детали ───────────────────────────

@router.get("/{manga_id}", response_class=HTMLResponse)
def manga_detail(manga_id: int, request: Request, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    m = _own_manga(manga_id, user, db)
    return templates.TemplateResponse("manga/detail.html", {
        "request": request, "user": user, "manga": m,
    })


@router.get("/{manga_id}/cover")
def manga_cover(manga_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    m = _own_manga(manga_id, user, db)
    if not m.cover_path:
        raise HTTPException(status_code=404)
    path = COVERS_DIR / m.cover_path
    if not path.exists():
        raise HTTPException(status_code=404)
    return FileResponse(path)


@router.get("/{manga_id}/read")
def manga_read_entry(manga_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    m = _own_manga(manga_id, user, db)
    if not m.chapters:
        return RedirectResponse(f"/manga/{manga_id}", status_code=302)
    # продолжить с сохранённой главы, иначе первая
    chapter_id = m.current_chapter_id
    if not any(c.id == chapter_id for c in m.chapters):
        chapter_id = m.chapters[0].id
    return RedirectResponse(f"/manga/{manga_id}/read/{chapter_id}", status_code=302)


@router.get("/{manga_id}/read/{chapter_id}", response_class=HTMLResponse)
def manga_reader(manga_id: int, chapter_id: int, request: Request,
                 user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    m = _own_manga(manga_id, user, db)
    chapter = _own_chapter(m, chapter_id, db)
    ordered = sorted(m.chapters, key=lambda c: c.order)
    idx = next((i for i, c in enumerate(ordered) if c.id == chapter.id), 0)
    prev_id = ordered[idx - 1].id if idx > 0 else None
    next_id = ordered[idx + 1].id if idx < len(ordered) - 1 else None
    start_page = m.current_page if m.current_chapter_id == chapter.id else 0
    return templates.TemplateResponse("manga/reader.html", {
        "request": request, "user": user, "manga": m, "chapter": chapter,
        "chapters": ordered, "page_count": chapter.page_count,
        "prev_id": prev_id, "next_id": next_id, "start_page": start_page,
    })


@router.get("/{manga_id}/read/{chapter_id}/page/{n}")
def manga_page(manga_id: int, chapter_id: int, n: int,
               user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    m = _own_manga(manga_id, user, db)
    chapter = _own_chapter(m, chapter_id, db)
    files = manga_service.page_files(m.folder, chapter.folder)
    if n < 1 or n > len(files):
        raise HTTPException(status_code=404)
    path = manga_service.chapter_dir(m.folder, chapter.folder) / files[n - 1]
    if not path.exists():
        raise HTTPException(status_code=404)
    # страницы неизменяемы → разрешаем долгое кэширование (меньше нагрузка на сервер)
    return FileResponse(path, headers={"Cache-Control": "private, max-age=604800"})


# ─────────────────────────── состояние / действия ───────────────────────────

@router.post("/{manga_id}/progress")
async def manga_progress(manga_id: int, request: Request,
                         user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    m = _own_manga(manga_id, user, db)
    try:
        data = await request.json()
    except Exception:
        data = {}
    cid = data.get("chapter_id")
    if cid and any(c.id == cid for c in m.chapters):
        m.current_chapter_id = int(cid)
    try:
        m.current_page = max(0, int(data.get("page", 0)))
    except (TypeError, ValueError):
        pass
    m.last_read_at = datetime.utcnow()
    db.commit()
    return JSONResponse({"ok": True})


@router.post("/{manga_id}/favorite")
def manga_favorite(manga_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    m = _own_manga(manga_id, user, db)
    m.is_favorite = not m.is_favorite
    db.commit()
    return RedirectResponse(f"/manga/{manga_id}", status_code=302)


@router.post("/{manga_id}/chapters/{chapter_id}/delete")
def delete_chapter(manga_id: int, chapter_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    m = _own_manga(manga_id, user, db)
    chapter = _own_chapter(m, chapter_id, db)
    manga_service.delete_chapter_dir(m.folder, chapter.folder)
    db.delete(chapter)
    db.commit()
    return RedirectResponse(f"/manga/{manga_id}", status_code=302)


@router.post("/{manga_id}/delete")
def delete_manga(manga_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    m = _own_manga(manga_id, user, db)
    manga_service.delete_manga_dir(m.folder)
    delete_file(m.cover_path, COVERS_DIR)
    db.delete(m)
    db.commit()
    return RedirectResponse("/manga", status_code=302)
