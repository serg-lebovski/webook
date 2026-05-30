"""Хранение аудиокниг и извлечение метаданных/длительности глав."""
import re
import shutil
import uuid
from pathlib import Path

from app.config import AUDIOBOOKS_DIR


def new_folder() -> str:
    """Создаёт уникальную папку для аудиокниги, возвращает её имя."""
    name = uuid.uuid4().hex
    (AUDIOBOOKS_DIR / name).mkdir(parents=True, exist_ok=True)
    return name


def book_dir(folder: str) -> Path:
    return AUDIOBOOKS_DIR / folder


def safe_track_name(original: str, fallback_ext: str = ".mp3") -> str:
    """Безопасное уникальное имя файла главы внутри папки книги."""
    suffix = Path(original).suffix.lower() or fallback_ext
    return f"{uuid.uuid4().hex}{suffix}"


_num_re = re.compile(r"(\d+)")


def natural_sort_key(name: str):
    """Ключ натуральной сортировки: 'ch2' < 'ch10'."""
    return [int(t) if t.isdigit() else t.lower() for t in _num_re.split(name)]


def nice_track_title(original: str) -> str:
    """Человекочитаемое название главы из имени файла."""
    stem = Path(original).stem
    stem = re.sub(r"[_]+", " ", stem).strip()
    return stem or original


def probe_audio(path: Path) -> dict:
    """Длительность и теги аудиофайла (best-effort, через mutagen, если есть)."""
    info = {"duration": None, "title": "", "author": "", "narrator": "",
            "album": "", "cover_data": None}
    try:
        from mutagen import File as MFile  # type: ignore

        mf = MFile(str(path))
        if mf is None:
            return info
        if getattr(mf, "info", None) is not None and getattr(mf.info, "length", None):
            info["duration"] = float(mf.info.length)

        tags = mf.tags or {}

        def first(*keys):
            for k in keys:
                try:
                    v = tags.get(k)
                except Exception:
                    v = None
                if v:
                    return str(v[0]) if isinstance(v, list) else str(v)
            return ""

        info["title"] = first("\xa9nam", "TIT2", "title")
        info["author"] = first("\xa9ART", "TPE1", "aART", "TPE2", "artist", "author")
        info["narrator"] = first("\xa9wrt", "TCOM", "composer")
        info["album"] = first("\xa9alb", "TALB", "album")

        # Встроенная обложка: MP4 'covr' или ID3 APIC
        try:
            covr = tags.get("covr")
            if covr:
                info["cover_data"] = bytes(covr[0])
            else:
                for k in (tags.keys() if hasattr(tags, "keys") else []):
                    if str(k).startswith("APIC"):
                        info["cover_data"] = tags[k].data
                        break
        except Exception:
            pass
    except Exception:
        pass
    return info


def delete_audiobook_folder(folder: str):
    if not folder:
        return
    p = AUDIOBOOKS_DIR / folder
    if p.exists():
        shutil.rmtree(p, ignore_errors=True)
