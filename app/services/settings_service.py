from sqlalchemy.orm import Session
from app.models.site_settings import SiteSettings


def get_setting(db: Session, key: str, default: str = "") -> str:
    row = db.query(SiteSettings).filter_by(key=key).first()
    return row.value if row else default


def set_setting(db: Session, key: str, value: str) -> None:
    row = db.query(SiteSettings).filter_by(key=key).first()
    if row:
        row.value = value
    else:
        db.add(SiteSettings(key=key, value=value))
    db.commit()


def is_registration_allowed(db: Session) -> bool:
    return get_setting(db, "allow_registration", "false") == "true"


def get_max_file_mb(db: Session) -> int:
    """Лимит размера загружаемого файла (в МБ) для файловой шары.
    Хранится в SiteSettings ключом max_file_mb; по умолчанию — из конфига."""
    from app.config import MAX_FILE_SIZE
    default = MAX_FILE_SIZE // (1024 * 1024)
    raw = get_setting(db, "max_file_mb", str(default))
    try:
        val = int(raw)
        return val if val > 0 else default
    except (TypeError, ValueError):
        return default


def get_max_file_bytes(db: Session) -> int:
    return get_max_file_mb(db) * 1024 * 1024
