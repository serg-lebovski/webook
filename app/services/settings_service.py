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
