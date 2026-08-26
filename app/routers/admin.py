import os
import shutil
import tempfile
from datetime import datetime
from fastapi import APIRouter, Depends, Form, Request, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse, FileResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from starlette.background import BackgroundTask

from app.dependencies import get_db, get_current_user
from app.models.user import User
from app.models.login_attempt import IpBan, LoginAttempt
from app.services.auth_service import hash_password
from app.services.settings_service import get_setting, set_setting
from app.services import update_service
from app.logging_config import auth_log
from app.config import LOGS_DIR

router = APIRouter(prefix="/admin")
templates = Jinja2Templates(directory="app/templates")


def _humanbytes(n) -> str:
    n = float(n or 0)
    for unit in ("Б", "КБ", "МБ", "ГБ", "ТБ"):
        if n < 1024 or unit == "ТБ":
            return f"{n:.0f} {unit}" if unit in ("Б", "КБ") else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} ТБ"


templates.env.filters["humanbytes"] = _humanbytes
templates.env.globals["UPDATE_IN_PROGRESS_STATES"] = update_service.IN_PROGRESS_STATES
templates.env.globals["UPDATE_STAGE_LABELS"] = update_service.STAGE_LABELS

LOG_VIEW_FILES = {
    "errors": "errors.log",
    "db": "db.log",
    "auth": "auth.log",
    "actions": "actions.log",
    "access": "access.log",
}


def _get_system_info() -> dict:
    from app.config import (DATABASE_URL, BOOKS_DIR, COVERS_DIR, LINKS_CONTENT_DIR,
                            AUDIOBOOKS_DIR, MANGA_DIR, BACKUPS_DIR)

    db_type = "PostgreSQL" if DATABASE_URL.startswith("postgresql") else "SQLite"

    # Mask password in connection string for display
    db_display = DATABASE_URL
    db_user = ""
    db_host = ""
    db_name = ""
    if "://" in DATABASE_URL:
        proto, rest = DATABASE_URL.split("://", 1)
        if "@" in rest:
            userinfo, hostpath = rest.rsplit("@", 1)
            db_user = userinfo.split(":")[0]
            db_display = f"{proto}://{db_user}:***@{hostpath}"
            host_port, db_name = (hostpath.split("/", 1) + [""])[:2]
            db_host = host_port
        else:
            db_name = rest.lstrip("./")

    def dir_size_bytes(path):
        if not path.exists():
            return 0
        return sum(f.stat().st_size for f in path.rglob("*") if f.is_file())

    books_bytes = dir_size_bytes(BOOKS_DIR)
    covers_bytes = dir_size_bytes(COVERS_DIR)
    links_bytes = dir_size_bytes(LINKS_CONTENT_DIR)
    audiobooks_bytes = dir_size_bytes(AUDIOBOOKS_DIR)
    manga_bytes = dir_size_bytes(MANGA_DIR)
    content_bytes = (books_bytes + covers_bytes + links_bytes
                     + audiobooks_bytes + manga_bytes)

    try:
        check_path = BOOKS_DIR if BOOKS_DIR.exists() else BOOKS_DIR.parent
        disk = shutil.disk_usage(check_path)
    except Exception:
        disk = shutil.disk_usage(".")

    # Информация о бэкапах БД (host-cron pg_backup.sh → *.sql.gz)
    backup_last_name = ""
    backup_last_bytes = 0
    backup_last_mtime = None
    backup_count = 0
    backup_total_bytes = 0
    try:
        if BACKUPS_DIR.exists():
            dumps = sorted(BACKUPS_DIR.glob("*.sql.gz"), key=lambda f: f.stat().st_mtime, reverse=True)
            backup_count = len(dumps)
            backup_total_bytes = sum(f.stat().st_size for f in dumps)
            if dumps:
                latest = dumps[0]
                st = latest.stat()
                backup_last_name = latest.name
                backup_last_bytes = st.st_size
                backup_last_mtime = datetime.fromtimestamp(st.st_mtime)
    except Exception:
        pass

    return {
        "db_type": db_type,
        "db_display": db_display,
        "db_user": db_user,
        "db_host": db_host,
        "db_name": db_name,
        "books_dir": str(BOOKS_DIR),
        "covers_dir": str(COVERS_DIR),
        "links_dir": str(LINKS_CONTENT_DIR),
        "audiobooks_dir": str(AUDIOBOOKS_DIR),
        "manga_dir": str(MANGA_DIR),
        "books_bytes": books_bytes,
        "covers_bytes": covers_bytes,
        "links_bytes": links_bytes,
        "audiobooks_bytes": audiobooks_bytes,
        "manga_bytes": manga_bytes,
        "content_bytes": content_bytes,
        "disk_total": disk.total,
        "disk_used": disk.used,
        "disk_free": disk.free,
        "backup_last_name": backup_last_name,
        "backup_last_bytes": backup_last_bytes,
        "backup_last_mtime": backup_last_mtime,
        "backup_count": backup_count,
        "backup_total_bytes": backup_total_bytes,
    }


def _require_admin(user: User):
    if not user.is_admin:
        raise HTTPException(status_code=403, detail="Только для администраторов")


@router.post("/update")
def trigger_update(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Запросить проверку обновлений: пишем файл-триггер, host-watcher проверит
    origin/master и, если есть новые коммиты, сам обновится."""
    _require_admin(user)
    try:
        update_service.trigger_check()
        auth_log.info("update check requested by %s", user.username)
        return RedirectResponse("/admin?success=update_queued", status_code=302)
    except Exception:
        return RedirectResponse("/admin?error=update_failed", status_code=302)


@router.get("/update/status")
def update_status(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """JSON-статус для живого поллинга прогресса обновления (JS на странице)."""
    _require_admin(user)
    state = update_service.read_state()
    stage = None
    if state["status"]:
        stage = update_service.STAGE_LABELS.get(state["status"]["state"], state["status"]["state"])
    return JSONResponse({
        "status": state["status"],
        "available": state["available"],
        "version": state["version"],
        "queued": state["queued"],
        "in_progress": bool(state["status"] and state["status"]["state"] in update_service.IN_PROGRESS_STATES),
        "stage_label": stage,
    })


def _cleanup(path: str):
    if path and os.path.exists(path):
        os.unlink(path)


@router.get("/backup/db")
def backup_db(token: str = "", user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Скачать дамп базы данных (только администратор)."""
    _require_admin(user)
    from app.services import archive_service
    fd, tmp = tempfile.mkstemp(prefix="webook_db_", suffix=".sql")
    os.close(fd)
    try:
        kind = archive_service.db_dump(tmp)
    except Exception as e:
        _cleanup(tmp)
        raise HTTPException(status_code=500, detail=f"Не удалось снять дамп БД: {e}")
    date_str = datetime.utcnow().strftime("%Y-%m-%d")
    ext = "sql" if kind == "postgresql" else "sqlite"
    auth_log.info("admin %s downloaded DB dump (%s)", user.username, kind)
    resp = FileResponse(tmp, media_type="application/octet-stream",
                        filename=f"webook_db_{date_str}.{ext}",
                        background=BackgroundTask(lambda: _cleanup(tmp)))
    if token:
        resp.set_cookie("backup_ready", token, max_age=300, path="/admin", samesite="lax")
    return resp


@router.get("/backup/full")
def backup_full(token: str = "", user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Полный серверный архив: дамп БД + все файлы (только администратор)."""
    _require_admin(user)
    from app.services import archive_service
    fd, tmp = tempfile.mkstemp(prefix="webook_full_", suffix=".zip")
    os.close(fd)
    try:
        archive_service.full_export(db, tmp)
    except Exception as e:
        _cleanup(tmp)
        raise HTTPException(status_code=500, detail=f"Не удалось собрать архив: {e}")
    date_str = datetime.utcnow().strftime("%Y-%m-%d")
    auth_log.info("admin %s downloaded FULL server backup", user.username)
    resp = FileResponse(tmp, media_type="application/zip",
                        filename=f"webook_full_backup_{date_str}.zip",
                        background=BackgroundTask(lambda: _cleanup(tmp)))
    if token:
        resp.set_cookie("backup_ready", token, max_age=300, path="/admin", samesite="lax")
    return resp


@router.get("", response_class=HTMLResponse)
def admin_page(
    request: Request,
    success: str = "",
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _require_admin(user)
    users = db.query(User).order_by(User.created_at).all()
    allow_registration = get_setting(db, "allow_registration", "false") == "true"
    steam_api_key = get_setting(db, "steam_api_key", "")
    s3_endpoint = get_setting(db, "s3_endpoint", "")
    s3_bucket = get_setting(db, "s3_bucket", "")
    s3_access_key = get_setting(db, "s3_access_key", "")
    s3_secret_key = get_setting(db, "s3_secret_key", "")
    system = _get_system_info()
    bans = (
        db.query(IpBan)
        .filter(IpBan.until > datetime.utcnow())
        .order_by(IpBan.until.desc())
        .all()
    )
    return templates.TemplateResponse("admin/index.html", {
        "request": request,
        "user": user,
        "users": users,
        "allow_registration": allow_registration,
        "steam_api_key": steam_api_key,
        "s3_endpoint": s3_endpoint,
        "s3_bucket": s3_bucket,
        "s3_access_key": s3_access_key,
        "s3_secret_key": s3_secret_key,
        "success": success,
        "error": None,
        "system": system,
        "now": datetime.utcnow(),
        "bans": bans,
        "update": update_service.read_state(),
    })


@router.post("/settings")
def update_admin_settings(
    request: Request,
    allow_registration: str = Form("off"),
    steam_api_key: str = Form(""),
    s3_endpoint: str = Form(""),
    s3_bucket: str = Form(""),
    s3_access_key: str = Form(""),
    s3_secret_key: str = Form(""),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _require_admin(user)
    value = "true" if allow_registration == "on" else "false"
    set_setting(db, "allow_registration", value)
    set_setting(db, "steam_api_key", steam_api_key.strip())
    set_setting(db, "s3_endpoint", s3_endpoint.strip())
    set_setting(db, "s3_bucket", s3_bucket.strip())
    set_setting(db, "s3_access_key", s3_access_key.strip())
    if s3_secret_key.strip():
        set_setting(db, "s3_secret_key", s3_secret_key.strip())
    return RedirectResponse("/admin?success=settings", status_code=302)


@router.get("/logs", response_class=HTMLResponse)
def view_logs(
    request: Request,
    file: str = "errors",
    n: int = 200,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _require_admin(user)
    if file not in LOG_VIEW_FILES:
        file = "errors"
    n = max(20, min(2000, n))
    path = LOGS_DIR / LOG_VIEW_FILES[file]
    lines = []
    if path.exists():
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()[-n:]
        except Exception:
            lines = ["(не удалось прочитать файл логов)"]
    lines.reverse()  # новые сверху
    return templates.TemplateResponse("admin/logs.html", {
        "request": request, "user": user,
        "files": list(LOG_VIEW_FILES.keys()),
        "current": file, "n": n, "lines": lines,
    })


@router.post("/bans/unban")
def unban_ip(
    ip: str = Form(...),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _require_admin(user)
    ip = ip.strip()
    db.query(IpBan).filter(IpBan.ip == ip).delete(synchronize_session=False)
    db.query(LoginAttempt).filter(LoginAttempt.ip == ip).delete(synchronize_session=False)
    db.commit()
    auth_log.info("admin %s UNBANNED ip=%s", user.username, ip)
    return RedirectResponse("/admin?success=unbanned", status_code=302)


@router.get("/users/new", response_class=HTMLResponse)
def create_user_page(
    request: Request,
    user: User = Depends(get_current_user),
):
    _require_admin(user)
    return templates.TemplateResponse("admin/user_form.html", {
        "request": request,
        "user": user,
        "edit_user": None,
        "error": None,
    })


@router.post("/users/new")
def create_user(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    is_admin: str = Form("off"),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _require_admin(user)

    if len(username.strip()) < 3:
        return templates.TemplateResponse("admin/user_form.html", {
            "request": request, "user": user, "edit_user": None,
            "error": "Логин минимум 3 символа",
        }, status_code=400)

    if len(password) < 6:
        return templates.TemplateResponse("admin/user_form.html", {
            "request": request, "user": user, "edit_user": None,
            "error": "Пароль минимум 6 символов",
        }, status_code=400)

    exists = db.query(User).filter_by(username=username.strip()).first()
    if exists:
        return templates.TemplateResponse("admin/user_form.html", {
            "request": request, "user": user, "edit_user": None,
            "error": "Пользователь с таким логином уже существует",
        }, status_code=400)

    new_user = User(
        username=username.strip(),
        password_hash=hash_password(password),
        is_admin=(is_admin == "on"),
    )
    db.add(new_user)
    db.commit()
    auth_log.info("admin %s CREATED user=%s admin=%s", user.username, new_user.username, new_user.is_admin)
    return RedirectResponse("/admin?success=user_created", status_code=302)


@router.get("/users/{user_id}/edit", response_class=HTMLResponse)
def edit_user_page(
    user_id: int,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _require_admin(user)
    edit_user = db.query(User).filter_by(id=user_id).first()
    if not edit_user:
        raise HTTPException(status_code=404, detail="Пользователь не найден")
    return templates.TemplateResponse("admin/user_form.html", {
        "request": request,
        "user": user,
        "edit_user": edit_user,
        "error": None,
    })


@router.post("/users/{user_id}/edit")
def edit_user(
    user_id: int,
    request: Request,
    username: str = Form(...),
    password: str = Form(""),
    is_admin: str = Form("off"),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _require_admin(user)
    edit_user = db.query(User).filter_by(id=user_id).first()
    if not edit_user:
        raise HTTPException(status_code=404, detail="Пользователь не найден")

    def err(msg):
        return templates.TemplateResponse("admin/user_form.html", {
            "request": request, "user": user, "edit_user": edit_user, "error": msg,
        }, status_code=400)

    username = username.strip()
    if len(username) < 3:
        return err("Логин минимум 3 символа")

    exists = db.query(User).filter(User.username == username, User.id != user_id).first()
    if exists:
        return err("Этот логин уже занят")

    edit_user.username = username
    edit_user.is_admin = (is_admin == "on")

    if password:
        if len(password) < 6:
            return err("Пароль минимум 6 символов")
        edit_user.password_hash = hash_password(password)

    db.commit()
    auth_log.info("admin %s EDITED user id=%s username=%s admin=%s pwd_changed=%s",
                  user.username, user_id, edit_user.username, edit_user.is_admin, bool(password))
    return RedirectResponse("/admin?success=user_updated", status_code=302)


@router.post("/users/{user_id}/delete")
def delete_user(
    user_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _require_admin(user)
    if user_id == user.id:
        raise HTTPException(status_code=400, detail="Нельзя удалить самого себя")
    target = db.query(User).filter_by(id=user_id).first()
    if not target:
        raise HTTPException(status_code=404, detail="Пользователь не найден")
    _purge_user_data(user_id, db)
    db.delete(target)
    db.commit()
    auth_log.warning("admin %s DELETED user id=%s username=%s", user.username, user_id, target.username)
    return RedirectResponse("/admin?success=user_deleted", status_code=302)


def _purge_user_data(uid: int, db: Session):
    """Delete all rows and files belonging to a user, in FK-safe order."""
    from app.models.book import Book
    from app.models.link import Link, LinkFolder
    from app.models.share import Share
    from app.models.shelf import Shelf
    from app.models.read_progress import ReadProgress
    from app.models.tag import Tag
    from app.models.highlight import Highlight
    from app.models.feed import Feed
    from app.services.book_service import delete_file
    from app.config import BOOKS_DIR, COVERS_DIR, LINKS_CONTENT_DIR

    # collect ids before bulk-deleting rows
    book_ids = [r.id for r in db.query(Book.id).filter_by(user_id=uid).all()]
    link_ids = [r.id for r in db.query(Link.id).filter_by(user_id=uid).all()]

    # 1. reading progress (user as reader + other readers on user's books)
    db.query(ReadProgress).filter(ReadProgress.user_id == uid).delete(synchronize_session=False)
    if book_ids:
        db.query(ReadProgress).filter(
            ReadProgress.book_id.in_(book_ids)
        ).delete(synchronize_session=False)

    # 2. shares (owned or received)
    db.query(Share).filter(
        (Share.owner_id == uid) | (Share.shared_with_user_id == uid)
    ).delete(synchronize_session=False)

    # 3. book files on disk, then book rows
    for book in db.query(Book).filter_by(user_id=uid).all():
        delete_file(book.file_path, BOOKS_DIR)
        delete_file(book.cover_path, COVERS_DIR)
    db.query(Book).filter_by(user_id=uid).delete(synchronize_session=False)

    # 4. link content files on disk, then link rows
    for link_id in link_ids:
        (LINKS_CONTENT_DIR / f"{link_id}.txt").unlink(missing_ok=True)
    db.query(Link).filter_by(user_id=uid).delete(synchronize_session=False)

    # 5. feeds (reference link_folders) then link folders (links already gone)
    db.query(Feed).filter_by(user_id=uid).delete(synchronize_session=False)
    db.query(LinkFolder).filter_by(user_id=uid).delete(synchronize_session=False)

    # 6. shelves (books already gone)
    db.query(Shelf).filter_by(user_id=uid).delete(synchronize_session=False)

    # 7. tags (book_tags/link_tags cascade via ondelete on book/link delete above)
    db.query(Tag).filter_by(user_id=uid).delete(synchronize_session=False)

    # 8. highlights
    db.query(Highlight).filter_by(user_id=uid).delete(synchronize_session=False)

    # 9. series tiers (личный тир-лист)
    from app.models.series_tier import SeriesTier
    db.query(SeriesTier).filter_by(user_id=uid).delete(synchronize_session=False)

    # 12. манга: каталоги на диске + строки (главы каскадом)
    from app.models.manga import Manga
    from app.services.manga_service import delete_manga_dir
    from app.services.book_service import delete_file as _del_cover
    from app.config import COVERS_DIR as _COVERS
    for mg in db.query(Manga).filter_by(user_id=uid).all():
        delete_manga_dir(mg.folder)
        _del_cover(mg.cover_path, _COVERS)
        db.delete(mg)

    # 13. аудиокниги: папки + обложки + строки (треки каскадом)
    from app.models.audiobook import Audiobook
    from app.services.audiobook_service import delete_audiobook_folder
    for ab in db.query(Audiobook).filter_by(user_id=uid).all():
        delete_audiobook_folder(ab.folder)
        _del_cover(ab.cover_path, _COVERS)
        db.delete(ab)

    # 14. подборки (элементы каскадом)
    from app.models.collection import Collection
    db.query(Collection).filter_by(user_id=uid).delete(synchronize_session=False)

    # 15. игры: обложки + строки (+ тир-лист каскадом)
    from app.models.game import Game, GameTier
    db.query(GameTier).filter_by(user_id=uid).delete(synchronize_session=False)
    for gm in db.query(Game).filter_by(user_id=uid).all():
        _del_cover(gm.cover_path, _COVERS)
        db.delete(gm)

    db.flush()
