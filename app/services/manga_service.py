"""Работа с файлами манги: сохранение страниц, распаковка CBZ/ZIP, сортировка."""
import io
import re
import shutil
import uuid
import zipfile
from pathlib import Path

from app.config import (MANGA_DIR, ALLOWED_MANGA_PAGE_FORMATS,
                        ALLOWED_MANGA_ARCHIVE_FORMATS)


def _natural_key(name: str):
    """Естественная сортировка: '2.jpg' < '10.jpg'."""
    return [int(t) if t.isdigit() else t.lower()
            for t in re.split(r"(\d+)", name)]


def new_folder() -> str:
    return uuid.uuid4().hex


def manga_dir(folder: str) -> Path:
    return MANGA_DIR / folder


def chapter_dir(manga_folder: str, chapter_folder: str) -> Path:
    return MANGA_DIR / manga_folder / chapter_folder


def expand_uploads(items) -> list:
    """items: список (filename, bytes). Архивы (.cbz/.zip) распаковываются в страницы.
    Возвращает естественно отсортированный список (name, bytes) только изображений."""
    pages = []
    for name, data in items:
        suffix = Path(name).suffix.lower()
        if suffix in ALLOWED_MANGA_ARCHIVE_FORMATS:
            try:
                with zipfile.ZipFile(io.BytesIO(data)) as zf:
                    for info in zf.infolist():
                        if info.is_dir():
                            continue
                        if Path(info.filename).suffix.lower() in ALLOWED_MANGA_PAGE_FORMATS:
                            pages.append((Path(info.filename).name, zf.read(info)))
            except zipfile.BadZipFile:
                continue
        elif suffix in ALLOWED_MANGA_PAGE_FORMATS:
            pages.append((Path(name).name, data))
    pages.sort(key=lambda p: _natural_key(p[0]))
    return pages


def save_chapter(manga_folder: str, images) -> tuple:
    """Сохраняет страницы главы в новый подкаталог. Возвращает (chapter_folder, count, first_rel)."""
    chapter_folder = new_folder()
    dest = chapter_dir(manga_folder, chapter_folder)
    dest.mkdir(parents=True, exist_ok=True)
    count = 0
    first = None
    for idx, (name, data) in enumerate(images, start=1):
        ext = Path(name).suffix.lower() or ".jpg"
        fname = f"{idx:04d}{ext}"
        (dest / fname).write_bytes(data)
        if first is None:
            first = fname
        count += 1
    return chapter_folder, count, first


def page_files(manga_folder: str, chapter_folder: str) -> list:
    d = chapter_dir(manga_folder, chapter_folder)
    if not d.exists():
        return []
    files = [f.name for f in d.iterdir() if f.is_file()]
    files.sort(key=_natural_key)
    return files


def delete_manga_dir(folder: str):
    shutil.rmtree(MANGA_DIR / folder, ignore_errors=True)


def delete_chapter_dir(manga_folder: str, chapter_folder: str):
    shutil.rmtree(chapter_dir(manga_folder, chapter_folder), ignore_errors=True)
