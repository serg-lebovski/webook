"""Двухшаговая загрузка книг: «разобрать» → «проверить и сохранить».

Шаг 1 (`stage_upload`): файл книги или ZIP-архив (в т.ч. «*.fb2.zip», архив из
нескольких книг, вложенные zip) кладётся во временную область STAGING_DIR.
Для каждой книги сохраняется `{token}.{ext}` + `{token}.json` (владелец, имя
файла, разобранные и нормализованные метаданные) + `{token}.jpg` (обложка).
Фронт получает карточки и даёт их поправить.

Шаг 2 (`commit`): по токенам переносим файлы в BOOKS_DIR/COVERS_DIR и создаём
строки Book. Повторная отправка не создаёт дублей — токен удаляется при коммите.

Брошенные файлы чистятся по TTL при каждом новом разборе.
"""
from __future__ import annotations

import json
import shutil
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Optional

from app.config import (
    STAGING_DIR, BOOKS_DIR, COVERS_DIR, ALLOWED_BOOK_FORMATS, MAX_BOOK_SIZE,
)
from app.services import book_meta_service
from app.services.book_service import (
    parse_book_file, expand_archive, is_archive_name,
)

STAGING_TTL = 24 * 3600
_TOKEN_LEN = 32


def _valid_token(token: str) -> bool:
    return len(token or "") == _TOKEN_LEN and all(c in "0123456789abcdef" for c in token)


def _paths(token: str) -> dict:
    return {
        "meta": STAGING_DIR / f"{token}.json",
        "cover": STAGING_DIR / f"{token}.jpg",
    }


def cleanup_expired() -> None:
    if not STAGING_DIR.exists():
        return
    cutoff = time.time() - STAGING_TTL
    for f in STAGING_DIR.iterdir():
        try:
            if f.is_file() and f.stat().st_mtime < cutoff:
                f.unlink(missing_ok=True)
        except OSError:
            pass


def _stage_one(user_id: int, filename: str, data: bytes, translate: bool) -> dict:
    suffix = Path(filename).suffix.lower()
    token = uuid.uuid4().hex
    STAGING_DIR.mkdir(parents=True, exist_ok=True)
    book_path = STAGING_DIR / f"{token}{suffix}"
    book_path.write_bytes(data)

    meta = parse_book_file(data, suffix) or {}
    cover_bytes = meta.pop("cover_data", None)
    has_cover = False
    if cover_bytes:
        # save_cover_file пишет в COVERS_DIR — нам нужен превью-файл в staging
        try:
            import io
            from PIL import Image
            img = Image.open(io.BytesIO(cover_bytes)).convert("RGB")
            img.thumbnail((400, 600))
            img.save(_paths(token)["cover"], format="JPEG", quality=85)
            has_cover = True
        except Exception:
            has_cover = False

    card = book_meta_service.normalize(meta, filename, translate=translate)
    card.update({
        "token": token, "filename": filename, "format": suffix.lstrip("."),
        "size": len(data), "has_cover": has_cover, "parsed": bool(meta),
    })
    record = {"user_id": user_id, "suffix": suffix, "created": time.time(), "card": card}
    _paths(token)["meta"].write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")
    return card


def stage_upload(user_id: int, filename: str, fileobj, size: int, translate: bool = True,
                 max_archive_size: int = 0) -> tuple[list, list]:
    """Разобрать один загруженный файл (книгу или архив). → (cards, errors)."""
    cleanup_expired()
    filename = Path(filename or "book").name
    suffix = Path(filename).suffix.lower()
    errors: list[str] = []
    if is_archive_name(filename):
        if max_archive_size and size > max_archive_size:
            return [], [f"{filename}: архив больше {max_archive_size // (1024 * 1024)} МБ"]
        entries, errs = expand_archive(fileobj, ALLOWED_BOOK_FORMATS, MAX_BOOK_SIZE)
        errors.extend(f"{filename}: {e}" for e in errs)
        if not entries and not errs:
            errors.append(f"{filename}: в архиве нет книг (epub, fb2, pdf)")
    elif suffix in ALLOWED_BOOK_FORMATS:
        if size > MAX_BOOK_SIZE:
            return [], [f"{filename}: файл больше {MAX_BOOK_SIZE // (1024 * 1024)} МБ"]
        entries = [(filename, fileobj.read())]
    else:
        return [], [f"{filename}: формат не поддерживается (нужны epub, fb2, pdf или архив zip/7z/rar)"]

    entries = [(n, d) for n, d in entries if d]
    # Перевод — сетевые запросы: обрабатываем книги архива параллельно.
    with ThreadPoolExecutor(max_workers=min(6, max(1, len(entries)))) as pool:
        futures = [pool.submit(_stage_one, user_id, n, d, translate) for n, d in entries]
        cards = []
        for (n, _), fut in zip(entries, futures):
            try:
                cards.append(fut.result())
            except Exception as e:
                errors.append(f"{n}: не удалось разобрать ({e.__class__.__name__})")
    return cards, errors


def load(token: str, user_id: int) -> Optional[dict]:
    if not _valid_token(token):
        return None
    p = _paths(token)["meta"]
    if not p.exists():
        return None
    try:
        rec = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if rec.get("user_id") != user_id:
        return None
    return rec


def cover_path(token: str, user_id: int) -> Optional[Path]:
    if not load(token, user_id):
        return None
    p = _paths(token)["cover"]
    return p if p.exists() else None


def take_files(token: str, rec: dict) -> tuple[Optional[str], Optional[str], int]:
    """Перенести книгу и обложку из staging в постоянное хранилище.
    → (file_name, cover_name, size). Удаляет staging-записи."""
    suffix = rec["suffix"]
    src = STAGING_DIR / f"{token}{suffix}"
    if not src.exists():
        return None, None, 0
    BOOKS_DIR.mkdir(parents=True, exist_ok=True)
    file_name = f"{uuid.uuid4().hex}{suffix}"
    size = src.stat().st_size
    shutil.move(str(src), str(BOOKS_DIR / file_name))

    cover_name = None
    cp = _paths(token)["cover"]
    if cp.exists():
        COVERS_DIR.mkdir(parents=True, exist_ok=True)
        cover_name = f"{uuid.uuid4().hex}.jpg"
        shutil.move(str(cp), str(COVERS_DIR / cover_name))
    _paths(token)["meta"].unlink(missing_ok=True)
    return file_name, cover_name, size


def discard(token: str, user_id: int) -> None:
    rec = load(token, user_id)
    if not rec:
        return
    (STAGING_DIR / f"{token}{rec['suffix']}").unlink(missing_ok=True)
    for p in _paths(token).values():
        p.unlink(missing_ok=True)

