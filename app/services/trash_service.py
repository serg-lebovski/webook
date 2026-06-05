"""Корзина (soft-delete) для книг, статей и файлов.

«Удаление» помечает запись `deleted_at` вместо физического удаления.
Восстановление обнуляет метку. Окончательное удаление (или авто-очистка
через RETENTION_DAYS) физически удаляет файлы, связи и строку.
"""
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from app.models.book import Book
from app.models.link import Link
from app.models.stored_file import StoredFile
from app.models.audiobook import Audiobook
from app.models.share import Share
from app.config import BOOKS_DIR, COVERS_DIR, LINKS_CONTENT_DIR, FILES_DIR
from app.services.book_service import delete_file

RETENTION_DAYS = 30


# ─────────────────────────── soft-delete ───────────────────────────

def trash_book(book: Book, db: Session) -> None:
    book.deleted_at = datetime.utcnow()


def trash_link(link: Link, db: Session) -> None:
    link.deleted_at = datetime.utcnow()


def trash_file(f: StoredFile, db: Session) -> None:
    f.deleted_at = datetime.utcnow()


def trash_audiobook(ab: Audiobook, db: Session) -> None:
    ab.deleted_at = datetime.utcnow()


# ─────────────────────────── restore ───────────────────────────

def restore_book(book: Book, db: Session) -> None:
    book.deleted_at = None


def restore_link(link: Link, db: Session) -> None:
    link.deleted_at = None


def restore_file(f: StoredFile, db: Session) -> None:
    f.deleted_at = None


def restore_audiobook(ab: Audiobook, db: Session) -> None:
    ab.deleted_at = None


# ─────────────────────────── окончательное удаление ───────────────────────────

def purge_book(book: Book, db: Session) -> None:
    delete_file(book.file_path, BOOKS_DIR)
    delete_file(book.cover_path, COVERS_DIR)
    db.query(Share).filter(Share.resource_type == "book", Share.resource_id == book.id)\
        .delete(synchronize_session=False)
    db.delete(book)


def purge_link(link: Link, db: Session) -> None:
    (LINKS_CONTENT_DIR / f"{link.id}.txt").unlink(missing_ok=True)
    db.query(Share).filter(Share.resource_type == "link", Share.resource_id == link.id)\
        .delete(synchronize_session=False)
    db.delete(link)


def purge_file(f: StoredFile, db: Session) -> None:
    (FILES_DIR / f.stored_name).unlink(missing_ok=True)
    db.query(Share).filter(Share.resource_type == "file", Share.resource_id == f.id)\
        .delete(synchronize_session=False)
    db.delete(f)


def purge_audiobook(ab: Audiobook, db: Session) -> None:
    from app.services.audiobook_service import delete_audiobook_folder
    delete_audiobook_folder(ab.folder)
    delete_file(ab.cover_path, COVERS_DIR)
    db.delete(ab)  # треки удалятся каскадом


# ─────────────────────────── авто-очистка ───────────────────────────

def purge_expired(db: Session, user_id: int = None) -> int:
    """Физически удаляет всё, что в корзине дольше RETENTION_DAYS. Возвращает число записей."""
    cutoff = datetime.utcnow() - timedelta(days=RETENTION_DAYS)
    n = 0
    for model, purge in ((Book, purge_book), (Link, purge_link),
                         (StoredFile, purge_file), (Audiobook, purge_audiobook)):
        q = db.query(model).filter(model.deleted_at.isnot(None), model.deleted_at < cutoff)
        if user_id is not None:
            q = q.filter(model.user_id == user_id)
        for row in q.all():
            purge(row, db)
            n += 1
    if n:
        db.commit()
    return n
