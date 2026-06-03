from sqlalchemy import create_engine, text
from sqlalchemy.orm import declarative_base, sessionmaker
from app.config import DATABASE_URL

_is_pg = DATABASE_URL.startswith("postgresql")

engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False} if not _is_pg else {},
    pool_pre_ping=True,
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


def init_db():
    from app.models import (  # noqa: F401
        user, shelf, author, series, book, link, site_settings, share,
        read_progress, login_attempt, tag, highlight, feed, audiobook, series_tier,
        workspace, stored_file,
    )
    Base.metadata.create_all(bind=engine)
    _migrate_db()
    _seed_site_settings()
    _seed_admin()


def _migrate_db():
    """Idempotent schema migrations for upgrades of existing databases."""
    if _is_pg:
        migrations = [
            "ALTER TABLE books ADD COLUMN IF NOT EXISTS user_id INTEGER REFERENCES users(id)",
            "ALTER TABLE books ADD COLUMN IF NOT EXISTS is_read BOOLEAN NOT NULL DEFAULT FALSE",
            "ALTER TABLE books ADD COLUMN IF NOT EXISTS read_at TIMESTAMP",
            "ALTER TABLE shares ADD COLUMN IF NOT EXISTS is_public BOOLEAN NOT NULL DEFAULT TRUE",
            "ALTER TABLE shares ADD COLUMN IF NOT EXISTS shared_with_user_id INTEGER REFERENCES users(id)",
            "ALTER TABLE shares ADD COLUMN IF NOT EXISTS expires_at TIMESTAMP",
            "ALTER TABLE links ADD COLUMN IF NOT EXISTS word_count INTEGER DEFAULT 0",
            "ALTER TABLE links ADD COLUMN IF NOT EXISTS read_progress DOUBLE PRECISION DEFAULT 0",
            "ALTER TABLE books ADD COLUMN IF NOT EXISTS rating INTEGER",
            "ALTER TABLE books ADD COLUMN IF NOT EXISTS is_favorite BOOLEAN NOT NULL DEFAULT FALSE",
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS reading_goal INTEGER DEFAULT 0",
            "ALTER TABLE books ADD COLUMN IF NOT EXISTS in_reading_list BOOLEAN NOT NULL DEFAULT FALSE",
            "ALTER TABLE books ADD COLUMN IF NOT EXISTS deleted_at TIMESTAMP",
            "ALTER TABLE links ADD COLUMN IF NOT EXISTS deleted_at TIMESTAMP",
            "ALTER TABLE stored_files ADD COLUMN IF NOT EXISTS deleted_at TIMESTAMP",
        ]
        backfill_expires = (
            "UPDATE shares SET expires_at = created_at + INTERVAL '7 days' WHERE expires_at IS NULL"
        )
    else:
        migrations = [
            "ALTER TABLE books ADD COLUMN user_id INTEGER REFERENCES users(id)",
            "ALTER TABLE books ADD COLUMN is_read INTEGER NOT NULL DEFAULT 0",
            "ALTER TABLE books ADD COLUMN read_at DATETIME",
            "ALTER TABLE shares ADD COLUMN is_public INTEGER NOT NULL DEFAULT 1",
            "ALTER TABLE shares ADD COLUMN shared_with_user_id INTEGER REFERENCES users(id)",
            "ALTER TABLE shares ADD COLUMN expires_at DATETIME",
            "ALTER TABLE links ADD COLUMN word_count INTEGER DEFAULT 0",
            "ALTER TABLE links ADD COLUMN read_progress REAL DEFAULT 0",
            "ALTER TABLE books ADD COLUMN rating INTEGER",
            "ALTER TABLE books ADD COLUMN is_favorite INTEGER NOT NULL DEFAULT 0",
            "ALTER TABLE users ADD COLUMN reading_goal INTEGER DEFAULT 0",
            "ALTER TABLE books ADD COLUMN in_reading_list INTEGER NOT NULL DEFAULT 0",
            "ALTER TABLE books ADD COLUMN deleted_at DATETIME",
            "ALTER TABLE links ADD COLUMN deleted_at DATETIME",
            "ALTER TABLE stored_files ADD COLUMN deleted_at DATETIME",
        ]
        backfill_expires = (
            "UPDATE shares SET expires_at = datetime(created_at, '+7 days') WHERE expires_at IS NULL"
        )

    with engine.connect() as conn:
        for sql in migrations:
            try:
                conn.execute(text(sql))
                conn.commit()
            except Exception:
                pass

        try:
            conn.execute(text(
                "UPDATE books SET user_id = ("
                "  SELECT s.user_id FROM shelves s WHERE s.id = books.shelf_id"
                ") WHERE user_id IS NULL"
            ))
            conn.commit()
        except Exception:
            pass

        try:
            conn.execute(text(backfill_expires))
            conn.commit()
        except Exception:
            pass


def _seed_site_settings():
    from app.models.site_settings import SiteSettings
    db = SessionLocal()
    try:
        if not db.query(SiteSettings).filter_by(key="allow_registration").first():
            db.add(SiteSettings(key="allow_registration", value="false"))
            db.commit()
    finally:
        db.close()


def _seed_admin():
    """Создаёт администратора при первой установке, если заданы ADMIN_USERNAME и
    ADMIN_PASSWORD в окружении и пользователей ещё нет. Иначе первый
    зарегистрировавшийся пользователь автоматически становится администратором."""
    import os
    username = os.getenv("ADMIN_USERNAME", "").strip()
    password = os.getenv("ADMIN_PASSWORD", "").strip()
    if not username or not password:
        return
    from app.models.user import User
    from app.services.auth_service import hash_password
    db = SessionLocal()
    try:
        if db.query(User).count() == 0:
            db.add(User(username=username, password_hash=hash_password(password), is_admin=True))
            db.commit()
    finally:
        db.close()
