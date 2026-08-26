import html as _html
import io
import os
import re
import tempfile
import zipfile
from datetime import datetime

from fastapi import APIRouter, Depends, Form, Request, UploadFile, File, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse, Response, FileResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from starlette.background import BackgroundTask

from app.dependencies import get_db, get_current_user
from app.models.user import User
from app.models.book import Book
from app.models.shelf import Shelf
from app.models.link import Link
from app.services.auth_service import hash_password, verify_password, create_access_token
from app.services import update_service
from app.logging_config import auth_log

router = APIRouter(prefix="/settings")
templates = Jinja2Templates(directory="app/templates")
templates.env.globals["UPDATE_IN_PROGRESS_STATES"] = update_service.IN_PROGRESS_STATES
templates.env.globals["UPDATE_STAGE_LABELS"] = update_service.STAGE_LABELS


def _get_stats(db: Session, user: User) -> dict:
    """Статистика и занятое место по всему контенту пользователя (счёт из БД)."""
    from sqlalchemy import func
    from app.models.audiobook import Audiobook, AudiobookTrack
    from app.config import LINKS_CONTENT_DIR

    uid = user.id

    def _sum(col, *filters):
        return int(db.query(func.coalesce(func.sum(col), 0)).filter(*filters).scalar() or 0)

    def _mb(n):
        return round(n / 1024 / 1024, 1)

    books_count = db.query(Book).filter_by(user_id=uid, deleted_at=None).count()
    articles_count = db.query(Link).filter_by(user_id=uid, deleted_at=None).count()
    audiobooks_count = db.query(Audiobook).filter_by(user_id=uid, deleted_at=None).count()
    shelves_count = db.query(Shelf).filter_by(user_id=uid).count()

    books_bytes = _sum(Book.file_size, Book.user_id == uid, Book.deleted_at.is_(None))
    audio_bytes = _sum(AudiobookTrack.file_size,
                       AudiobookTrack.audiobook_id == Audiobook.id, Audiobook.user_id == uid,
                       Audiobook.deleted_at.is_(None))

    articles_bytes = 0
    link_ids = db.query(Link.id).filter_by(user_id=uid, deleted_at=None).all()
    for (lid,) in link_ids:
        content_path = LINKS_CONTENT_DIR / f"{lid}.txt"
        if content_path.exists():
            articles_bytes += content_path.stat().st_size

    storage_bytes = books_bytes + audio_bytes + articles_bytes

    return {
        "books": books_count,
        "articles": articles_count,
        "audiobooks": audiobooks_count,
        "shelves": shelves_count,
        "books_mb": _mb(books_bytes),
        "audiobooks_mb": _mb(audio_bytes),
        "articles_mb": _mb(articles_bytes),
        "storage_mb": _mb(storage_bytes),
    }


@router.get("", response_class=HTMLResponse)
def settings_page(
    request: Request,
    success: str = "",
    error: str = "",
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    from app.services import cloud_backup_service
    stats = _get_stats(db, user)
    update = update_service.read_state() if user.is_admin else None
    return templates.TemplateResponse("settings.html", {
        "request": request,
        "user": user,
        "stats": stats,
        "success": success,
        "error": error or None,
        "update": update,
        "cloud_backup_configured": cloud_backup_service.is_configured(db),
        "cloud_backup_last": cloud_backup_service.last_backup_info(db, user.id),
    })


@router.post("/backup/cloud")
def backup_cloud(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Собирает тот же ZIP, что и /settings/backup, и грузит его в настроенное
    администратором S3-совместимое хранилище вместо отдачи браузеру."""
    from app.services import archive_service, cloud_backup_service
    fd, tmp_path = tempfile.mkstemp(prefix="webook_cloudbackup_", suffix=".zip")
    os.close(fd)
    try:
        archive_service.export_user(user, db, tmp_path)
        ok, msg = cloud_backup_service.upload_backup(db, user, tmp_path)
    finally:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)
    if ok:
        return RedirectResponse("/settings?success=cloud_backup", status_code=302)
    from urllib.parse import quote
    return RedirectResponse(f"/settings?error={quote(msg)}", status_code=302)


@router.post("/update")
def trigger_update(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Запросить проверку обновлений (только администратор): host-watcher проверит
    origin/master и, если есть новые коммиты, сам обновится."""
    if not user.is_admin:
        raise HTTPException(status_code=403, detail="Только для администраторов")
    try:
        update_service.trigger_check()
        auth_log.info("update check requested by %s", user.username)
        return RedirectResponse("/settings?success=update_queued", status_code=302)
    except Exception:
        return RedirectResponse("/settings?error=update_failed", status_code=302)


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
    stats = _get_stats(db, user)

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


@router.post("/steam")
def update_steam(
    steam_profile_url: str = Form(""),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Сохранить ссылку на профиль Steam и резолвить SteamID64 (для наигранного времени)."""
    from app.services import steam_service, settings_service
    user.steam_profile_url = steam_profile_url.strip()[:300]
    api_key = settings_service.get_setting(db, "steam_api_key", "")
    if user.steam_profile_url:
        sid = steam_service.resolve_steamid(user.steam_profile_url, api_key)
        if sid:
            user.steam_id = sid
    else:
        user.steam_id = ""
    db.commit()
    return RedirectResponse("/settings?success=steam", status_code=302)


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
        "request": request, "user": user, "stats": _get_stats(db, user),
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
            src = ext_dir / "icons" / f"icon{size}.png"
            if src.exists():
                zf.writestr(f"icons/icon{size}.png", src.read_bytes())
    buf.seek(0)
    return Response(
        content=buf.read(),
        media_type="application/zip",
        headers={"Content-Disposition": 'attachment; filename="webook-extension.zip"'},
    )


@router.get("/extension/reg/{browser}")
def extension_install_reg(
    browser: str,
    request: Request,
    user: User = Depends(get_current_user),
):
    """.reg-файл: политика ExtensionSettings ставит расширение автоматически
    (без zip / Load unpacked), используя /extension/updates.xml + /extension/webook.crx."""
    from app.routers.pwa import EXTENSION_ID
    host = request.url.hostname or "localhost"
    update_url = f"http://{host}:8000/extension/updates.xml"
    if browser == "yandex":
        key_path = r"SOFTWARE\Policies\Yandex\YandexBrowser"
        fname = "webook-extension-yandex.reg"
    elif browser == "chrome":
        key_path = r"SOFTWARE\Policies\Google\Chrome"
        fname = "webook-extension-chrome.reg"
    else:
        raise HTTPException(404)
    settings_json = (
        '{"' + EXTENSION_ID + '":{"installation_mode":"normal_installed","update_url":"' + update_url + '"}}'
    )
    escaped = settings_json.replace('\\', '\\\\').replace('"', '\\"')
    reg = (
        "Windows Registry Editor Version 5.00\r\n\r\n"
        f"[HKEY_LOCAL_MACHINE\\{key_path}]\r\n"
        f'"ExtensionSettings"="{escaped}"\r\n'
    )
    return Response(
        content=reg.encode("utf-8"),
        media_type="text/plain",
        headers={"Content-Disposition": f'attachment; filename="{fname}"'},
    )


@router.get("/app/download")
def app_download(user: User = Depends(get_current_user)):
    """Скачать установщик Android-приложения (APK), собранный заранее."""
    from pathlib import Path as _Path
    apk = _Path("static") / "webook.apk"
    if not apk.is_file():
        return Response(
            content="Android-приложение ещё не собрано. Обратитесь к администратору.",
            media_type="text/plain; charset=utf-8",
            status_code=404,
        )
    return FileResponse(
        str(apk),
        media_type="application/vnd.android.package-archive",
        filename="webook.apk",
    )
