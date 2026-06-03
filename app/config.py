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
WORKSPACE_DIR = Path(os.getenv("WORKSPACE_DIR", str(BASE_DIR / "workspace")))
LOGS_DIR = Path(os.getenv("LOGS_DIR", str(BASE_DIR / "logs")))
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")

ALLOWED_BOOK_FORMATS = {".epub", ".fb2", ".pdf"}
ALLOWED_IMAGE_FORMATS = {".jpg", ".jpeg", ".png", ".webp"}
ALLOWED_AUDIO_FORMATS = {".mp3", ".m4a", ".m4b", ".ogg", ".oga", ".opus", ".aac", ".flac", ".wav", ".webm"}
MAX_BOOK_SIZE = 100 * 1024 * 1024            # 100 MB
MAX_COVER_SIZE = 5 * 1024 * 1024             # 5 MB
MAX_AUDIO_SIZE = 1024 * 1024 * 1024          # 1 GB на файл

APP_TITLE = os.getenv("APP_TITLE", "WeBook")
