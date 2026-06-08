from pathlib import Path
from dotenv import load_dotenv
import os

load_dotenv()

SECRET_KEY = os.getenv("SECRET_KEY", "change-me-in-production-please")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60 * 24 * 7  # 7 days

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./webook.db")

# Base dir is the project root (parent of the app/ package)
BASE_DIR = Path(__file__).parent.parent

BOOKS_DIR = Path(os.getenv("BOOKS_DIR", str(BASE_DIR / "books")))
COVERS_DIR = Path(os.getenv("COVERS_DIR", str(BASE_DIR / "files")))
LINKS_CONTENT_DIR = Path(os.getenv("LINKS_DIR", str(BASE_DIR / "links")))
AUDIOBOOKS_DIR = Path(os.getenv("AUDIOBOOKS_DIR", str(BASE_DIR / "audiobooks")))
FILES_DIR = Path(os.getenv("FILES_DIR", str(BASE_DIR / "userfiles")))
MANGA_DIR = Path(os.getenv("MANGA_DIR", str(BASE_DIR / "manga")))
LOGS_DIR = Path(os.getenv("LOGS_DIR", str(BASE_DIR / "logs")))
BACKUPS_DIR = Path(os.getenv("BACKUPS_DIR", str(BASE_DIR / "backups")))
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")

ALLOWED_BOOK_FORMATS = {".epub", ".fb2", ".pdf"}
ALLOWED_IMAGE_FORMATS = {".jpg", ".jpeg", ".png", ".webp"}
ALLOWED_AUDIO_FORMATS = {".mp3", ".m4a", ".m4b", ".ogg", ".oga", ".opus", ".aac", ".flac", ".wav", ".webm"}
MAX_BOOK_SIZE = 100 * 1024 * 1024            # 100 MB
MAX_COVER_SIZE = 5 * 1024 * 1024             # 5 MB
MAX_AUDIO_SIZE = 1024 * 1024 * 1024          # 1 GB на файл
MAX_FILE_SIZE = 2 * 1024 * 1024 * 1024        # 2 GB на файл (файловая шара)
ALLOWED_MANGA_PAGE_FORMATS = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".avif"}
ALLOWED_MANGA_ARCHIVE_FORMATS = {".cbz", ".zip"}
MAX_MANGA_ARCHIVE_SIZE = 500 * 1024 * 1024    # 500 MB на загрузку главы

APP_TITLE = os.getenv("APP_TITLE", "WeBook")

# Смещение местного времени относительно UTC (часы). Сроки задач и напоминания
# заметок пользователь вводит по «настенным» часам; в БД они naive. Сервер живёт
# в UTC, поэтому для корректного сравнения добавляем смещение. МСК = UTC+3
# (без перехода на летнее время), поэтому фиксированного значения достаточно.
APP_TZ_OFFSET = int(os.getenv("APP_TZ_OFFSET", "3"))


def local_now():
    """Текущее местное время (naive) — для сравнения с «настенными» полями."""
    from datetime import datetime, timedelta
    return datetime.utcnow() + timedelta(hours=APP_TZ_OFFSET)
