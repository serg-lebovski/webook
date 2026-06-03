"""Рабочее пространство: задачи, заметки (markdown), тайм-менеджер."""
from datetime import datetime
from pathlib import Path
from typing import List, Optional

from fastapi import APIRouter, Depends, Form, Request, UploadFile, File, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse, FileResponse, Response
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.dependencies import get_db, get_current_user
from app.models.user import User
from app.models.workspace import Task, TaskImage, Note, TimeInterval
from app.services.book_service import save_upload, delete_file
from app.config import WORKSPACE_DIR, ALLOWED_IMAGE_FORMATS

router = APIRouter(prefix="/workspace")
templates = Jinja2Templates(directory="app/templates")


def _parse_dt(s: str):
    s = (s or "").strip()
    if not s:
        return None
    try:
        return datetime.fromisoformat(s)
    except ValueError:
        return None


# ─────────────────────────── картинки ───────────────────────────

@router.post("/upload-image")
async def upload_image(file: UploadFile = File(...), user: User = Depends(get_current_user)):
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in ALLOWED_IMAGE_FORMATS:
        raise HTTPException(status_code=400, detail="Неподдерживаемый формат изображения")
    data = await file.read()
    name = save_upload(data, suffix, WORKSPACE_DIR)
    return JSONResponse({"url": f"/workspace/image/{name}", "name": name})


@router.get("/image/{name}")
def serve_image(name: str, user: User = Depends(get_current_user)):
    safe = Path(name).name
    path = WORKSPACE_DIR / safe
    if not path.exists():
        raise HTTPException(status_code=404)
    return FileResponse(path)


# ─────────────────────────── корень ───────────────────────────

@router.get("", response_class=HTMLResponse)
@router.get("/", response_class=HTMLResponse)
def workspace_root():
    return RedirectResponse("/workspace/tasks", status_code=302)


# ─────────────────────────── Задачи ───────────────────────────

_STATUS_ORDER = {"doing": 0, "todo": 1, "done": 2}


@router.get("/tasks", response_class=HTMLResponse)
def tasks_page(request: Request, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    tasks = db.query(Task).filter(Task.user_id == user.id).all()
    tasks.sort(key=lambda t: (_STATUS_ORDER.get(t.status, 1),
                              t.due_at or datetime.max, -t.id))
    return templates.TemplateResponse("workspace/tasks.html", {
        "request": request, "user": user, "active": "tasks", "tasks": tasks,
        "now": datetime.utcnow(),
    })


async def _save_task_images(task: Task, files: List[UploadFile], db: Session):
    for f in files or []:
        if not f or not f.filename:
            continue
        suffix = Path(f.filename).suffix.lower()
        if suffix not in ALLOWED_IMAGE_FORMATS:
            continue
        data = await f.read()
        if not data:
            continue
        name = save_upload(data, suffix, WORKSPACE_DIR)
        db.add(TaskImage(task_id=task.id, filename=name))


@router.post("/tasks")
async def create_task(
    request: Request,
    title: str = Form(...),
    content: str = Form(""),
    status: str = Form("todo"),
    due_at: str = Form(""),
    images: List[UploadFile] = File([]),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if not title.strip():
        return RedirectResponse("/workspace/tasks", status_code=302)
    task = Task(user_id=user.id, title=title.strip(), content=content,
                status=status if status in ("todo", "doing", "done") else "todo",
                due_at=_parse_dt(due_at))
    db.add(task)
    db.flush()
    await _save_task_images(task, images, db)
    db.commit()
    return RedirectResponse("/workspace/tasks", status_code=302)


def _own_task(task_id: int, user: User, db: Session) -> Task:
    t = db.query(Task).filter_by(id=task_id, user_id=user.id).first()
    if not t:
        raise HTTPException(status_code=404)
    return t


@router.get("/tasks/{task_id}", response_class=HTMLResponse)
def task_edit_page(task_id: int, request: Request, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    task = _own_task(task_id, user, db)
    return templates.TemplateResponse("workspace/task_edit.html", {
        "request": request, "user": user, "active": "tasks", "task": task,
    })


@router.post("/tasks/{task_id}")
async def update_task(
    task_id: int,
    title: str = Form(...),
    content: str = Form(""),
    status: str = Form("todo"),
    due_at: str = Form(""),
    images: List[UploadFile] = File([]),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    task = _own_task(task_id, user, db)
    task.title = title.strip() or task.title
    task.content = content
    task.status = status if status in ("todo", "doing", "done") else task.status
    task.due_at = _parse_dt(due_at)
    task.updated_at = datetime.utcnow()
    await _save_task_images(task, images, db)
    db.commit()
    return RedirectResponse("/workspace/tasks", status_code=302)


@router.post("/tasks/{task_id}/status")
def task_status(task_id: int, status: str = Form(...), user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    task = _own_task(task_id, user, db)
    if status in ("todo", "doing", "done"):
        task.status = status
        task.updated_at = datetime.utcnow()
        db.commit()
    return RedirectResponse("/workspace/tasks", status_code=302)


@router.post("/tasks/{task_id}/delete")
def task_delete(task_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    task = _own_task(task_id, user, db)
    for img in task.images:
        delete_file(img.filename, WORKSPACE_DIR)
    db.delete(task)
    db.commit()
    return RedirectResponse("/workspace/tasks", status_code=302)


@router.post("/tasks/{task_id}/images/{img_id}/delete")
def task_image_delete(task_id: int, img_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    task = _own_task(task_id, user, db)
    img = db.query(TaskImage).filter_by(id=img_id, task_id=task.id).first()
    if img:
        delete_file(img.filename, WORKSPACE_DIR)
        db.delete(img)
        db.commit()
    return RedirectResponse(f"/workspace/tasks/{task_id}", status_code=302)


# ─────────────────────────── Заметки ───────────────────────────

@router.get("/notes", response_class=HTMLResponse)
def notes_page(request: Request, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    notes = db.query(Note).filter(Note.user_id == user.id).order_by(Note.updated_at.desc()).all()
    return templates.TemplateResponse("workspace/notes.html", {
        "request": request, "user": user, "active": "notes", "notes": notes,
    })


@router.post("/notes")
def create_note(title: str = Form("Без названия"), user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    note = Note(user_id=user.id, title=title.strip() or "Без названия", content="")
    db.add(note)
    db.commit()
    db.refresh(note)
    return RedirectResponse(f"/workspace/notes/{note.id}", status_code=302)


@router.post("/notes/import")
async def import_note(file: UploadFile = File(...), user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    data = await file.read()
    text = data.decode("utf-8", errors="replace")
    title = Path(file.filename or "Заметка").stem or "Заметка"
    # если первая строка — заголовок markdown, берём его как название
    for line in text.splitlines():
        if line.strip():
            if line.startswith("#"):
                title = line.lstrip("#").strip() or title
            break
    note = Note(user_id=user.id, title=title[:200], content=text)
    db.add(note)
    db.commit()
    db.refresh(note)
    return RedirectResponse(f"/workspace/notes/{note.id}", status_code=302)


def _own_note(note_id: int, user: User, db: Session) -> Note:
    n = db.query(Note).filter_by(id=note_id, user_id=user.id).first()
    if not n:
        raise HTTPException(status_code=404)
    return n


@router.get("/notes/{note_id}", response_class=HTMLResponse)
def note_edit_page(note_id: int, request: Request, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    note = _own_note(note_id, user, db)
    return templates.TemplateResponse("workspace/note_edit.html", {
        "request": request, "user": user, "active": "notes", "note": note,
    })


@router.post("/notes/{note_id}")
def save_note(
    note_id: int,
    title: str = Form(...),
    content: str = Form(""),
    remind_at: str = Form(""),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    note = _own_note(note_id, user, db)
    note.title = title.strip() or "Без названия"
    note.content = content
    note.remind_at = _parse_dt(remind_at)
    note.updated_at = datetime.utcnow()
    db.commit()
    return RedirectResponse(f"/workspace/notes/{note_id}", status_code=302)


@router.post("/notes/{note_id}/delete")
def delete_note(note_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    note = _own_note(note_id, user, db)
    db.delete(note)
    db.commit()
    return RedirectResponse("/workspace/notes", status_code=302)


@router.get("/notes/{note_id}/export")
def export_note(note_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    note = _own_note(note_id, user, db)
    import re
    fname = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", note.title).strip() or "note"
    body = note.content or ""
    if not body.lstrip().startswith("#"):
        body = f"# {note.title}\n\n{body}"
    return Response(content=body.encode("utf-8"), media_type="text/markdown; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="{fname}.md"'})


# ─────────────────────────── Тайм-менеджер ───────────────────────────

def _running(db: Session, user: User):
    return db.query(TimeInterval).filter(TimeInterval.user_id == user.id,
                                         TimeInterval.ended_at.is_(None)).first()


@router.get("/timer", response_class=HTMLResponse)
def timer_page(request: Request, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    from datetime import timedelta
    since = datetime.utcnow() - timedelta(hours=24)
    intervals = (
        db.query(TimeInterval)
        .filter(TimeInterval.user_id == user.id, TimeInterval.started_at >= since)
        .order_by(TimeInterval.started_at.desc())
        .all()
    )
    running = _running(db, user)
    return templates.TemplateResponse("workspace/timer.html", {
        "request": request, "user": user, "active": "timer",
        "intervals": intervals, "running": running,
    })


@router.post("/timer/start")
def timer_start(kind: str = Form("work"), target_minutes: str = Form(""),
                user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    if _running(db, user):
        return RedirectResponse("/workspace/timer", status_code=302)
    target = None
    try:
        if target_minutes.strip():
            target = max(1, int(float(target_minutes))) * 60
    except ValueError:
        target = None
    db.add(TimeInterval(user_id=user.id, started_at=datetime.utcnow(),
                        kind="rest" if kind == "rest" else "work", target_seconds=target))
    db.commit()
    return RedirectResponse("/workspace/timer", status_code=302)


@router.post("/timer/mark")
def timer_mark(label: str = Form(""), kind: str = Form("work"),
               user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Закрывает текущий отрезок (подписывая его) и открывает новый — не останавливая учёт."""
    cur = _running(db, user)
    now = datetime.utcnow()
    if cur:
        cur.ended_at = now
        if label.strip():
            cur.label = label.strip()
    db.add(TimeInterval(user_id=user.id, started_at=now,
                        kind="rest" if kind == "rest" else "work"))
    db.commit()
    return RedirectResponse("/workspace/timer", status_code=302)


@router.post("/timer/stop")
def timer_stop(label: str = Form(""), user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    cur = _running(db, user)
    if cur:
        cur.ended_at = datetime.utcnow()
        if label.strip():
            cur.label = label.strip()
        db.commit()
    return RedirectResponse("/workspace/timer", status_code=302)


@router.post("/timer/{interval_id}/label")
def timer_label(interval_id: int, label: str = Form(""),
                user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    iv = db.query(TimeInterval).filter_by(id=interval_id, user_id=user.id).first()
    if iv:
        iv.label = label.strip()
        db.commit()
    return RedirectResponse("/workspace/timer", status_code=302)


@router.post("/timer/{interval_id}/delete")
def timer_delete(interval_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    iv = db.query(TimeInterval).filter_by(id=interval_id, user_id=user.id).first()
    if iv:
        db.delete(iv)
        db.commit()
    return RedirectResponse("/workspace/timer", status_code=302)
