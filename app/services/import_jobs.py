"""Фоновые задачи импорта манги: загрузка в потоке с отслеживанием прогресса.

In-memory реестр (один процесс uvicorn). Запрос только ставит задачу и сразу
возвращается — поэтому нет 504. Прогресс опрашивается страницей по AJAX, а главы
сохраняются в БД по мере скачивания (частичный результат доступен сразу).
"""
from __future__ import annotations

import threading
import time
import uuid
from pathlib import Path

from app.database import SessionLocal
from app.models.manga import Manga, MangaChapter
from app.services import manga_service, manga_import_service
from app.services.book_service import save_cover_file

_jobs: dict[str, dict] = {}
_plans: dict[str, dict] = {}
_lock = threading.Lock()
_MAX_JOBS = 100


def save_plan(user_id: int, plan: dict) -> str:
    pid = uuid.uuid4().hex
    with _lock:
        _plans[pid] = {"user_id": user_id, "plan": plan, "created": time.time()}
        if len(_plans) > _MAX_JOBS:
            old = sorted(_plans.items(), key=lambda kv: kv[1]["created"])[: len(_plans) - _MAX_JOBS]
            for k, _ in old:
                _plans.pop(k, None)
    return pid


def get_plan(pid: str, user_id: int) -> dict | None:
    with _lock:
        p = _plans.get(pid)
    if not p or p["user_id"] != user_id:
        return None
    return p["plan"]


def _prune():
    if len(_jobs) <= _MAX_JOBS:
        return
    old = sorted(_jobs.items(), key=lambda kv: kv[1]["created"])[: len(_jobs) - _MAX_JOBS]
    for jid, _ in old:
        _jobs.pop(jid, None)


def create(user_id: int, manga_id: int, manga_folder: str, title: str,
           chapters: list[dict], paginate: bool, order_base: int) -> str:
    jid = uuid.uuid4().hex
    with _lock:
        _jobs[jid] = {
            "id": jid, "user_id": user_id, "manga_id": manga_id,
            "manga_folder": manga_folder, "title": title,
            "chapters": chapters, "paginate": paginate, "order_base": order_base,
            "status": "running", "chapters_total": len(chapters),
            "chapters_done": 0, "current_label": "", "pages_done": 0,
            "message": "", "cancel": False, "created": time.time(),
        }
        _prune()
    t = threading.Thread(target=_worker, args=(jid,), daemon=True)
    t.start()
    return jid


def get(jid: str) -> dict | None:
    with _lock:
        j = _jobs.get(jid)
        return dict(j) if j else None


def list_for_user(user_id: int) -> list[dict]:
    """Задачи пользователя, новые сверху (для страницы «Загрузки»)."""
    with _lock:
        jobs = [dict(j) for j in _jobs.values() if j["user_id"] == user_id]
    jobs.sort(key=lambda j: j["created"], reverse=True)
    return jobs


def _update(jid: str, **kw):
    with _lock:
        j = _jobs.get(jid)
        if j:
            j.update(kw)


def cancel(jid: str):
    _update(jid, cancel=True)


def _canceled(jid: str) -> bool:
    with _lock:
        j = _jobs.get(jid)
        return bool(j and j["cancel"])


def _worker(jid: str):
    job = get(jid)
    if not job:
        return
    db = SessionLocal()
    client = manga_import_service.make_client()
    budget = {"total": 0}
    try:
        for i, ch in enumerate(job["chapters"], start=1):
            if _canceled(jid):
                _update(jid, status="canceled", message="Отменено")
                break
            _update(jid, current_label=ch["label"], pages_done=0)
            try:
                imgs = manga_import_service.download_chapter(
                    client, ch["url"], job["paginate"], budget,
                    on_page=lambda n: _update(jid, pages_done=n),
                )
            except Exception:
                imgs = []
            if imgs:
                ch_folder, count, first = manga_service.save_chapter(job["manga_folder"], imgs)
                order = job["order_base"] + i
                db.add(MangaChapter(manga_id=job["manga_id"], title=ch["label"],
                                    order=order, folder=ch_folder, page_count=count))
                m = db.query(Manga).filter_by(id=job["manga_id"]).first()
                if m is not None and not m.cover_path and first:
                    fb = (manga_service.chapter_dir(job["manga_folder"], ch_folder) / first).read_bytes()
                    m.cover_path = save_cover_file(fb, Path(first).suffix.lower() or ".jpg")
                db.commit()
            _update(jid, chapters_done=i)
        else:
            _update(jid, status="done", message="Готово")
        if budget["total"] > manga_import_service._MAX_TOTAL_BYTES:
            _update(jid, status="done", message="Достигнут лимит объёма")
    except Exception as e:
        _update(jid, status="error", message=str(e))
    finally:
        try:
            client.close()
        except Exception:
            pass
        db.close()
