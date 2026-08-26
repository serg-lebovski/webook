"""Импорт библиотеки из CSV-экспорта Goodreads (тот же формат частично подходит
для LibraryThing). Использует уже существующие _get_or_create_* хелперы книг и,
при наличии ISBN, обогащает недостающие метаданные/обложку через isbn_service —
как и штатный ручной ISBN-импорт."""
import csv
import io
import re
from datetime import datetime

from sqlalchemy.orm import Session

from app.models.book import Book
from app.models.user import User
from app.services.isbn_service import fetch_by_isbn, download_cover, normalize_isbn
from app.services.book_service import save_cover_file

DEFAULT_SHELF_NAME = "Импорт из Goodreads"


def _clean(v: str) -> str:
    """Goodreads оборачивает ISBN в ="1234567890" — снимаем формулу и кавычки."""
    v = (v or "").strip()
    m = re.match(r'^="?(.*?)"?$', v)
    return m.group(1) if m else v


def _parse_date(v: str):
    v = _clean(v)
    if not v:
        return None
    try:
        return datetime.strptime(v, "%Y/%m/%d")
    except ValueError:
        return None


def import_goodreads_csv(db: Session, user: User, csv_bytes: bytes, get_or_create_author, get_or_create_shelf) -> dict:
    """Возвращает {"imported": int, "skipped": int, "total": int}."""
    text = csv_bytes.decode("utf-8-sig", errors="replace")
    reader = csv.DictReader(io.StringIO(text))

    existing = {
        (b.title.strip().lower(), (b.author.name.strip().lower() if b.author else ""))
        for b in db.query(Book).filter(Book.user_id == user.id, Book.deleted_at.is_(None)).all()
    }

    imported = skipped = total = 0
    for row in reader:
        title = _clean(row.get("Title", ""))
        author_name = _clean(row.get("Author", ""))
        if not title:
            continue
        total += 1
        key = (title.strip().lower(), author_name.strip().lower())
        if key in existing:
            skipped += 1
            continue

        isbn = normalize_isbn(_clean(row.get("ISBN13", "")) or _clean(row.get("ISBN", "")))
        meta = fetch_by_isbn(isbn) if isbn else None

        author = get_or_create_author(author_name or (meta or {}).get("author") or "", db)
        exclusive_shelf = _clean(row.get("Exclusive Shelf", "")).lower()
        shelf = get_or_create_shelf(DEFAULT_SHELF_NAME, user, db)

        rating = None
        try:
            r = int(_clean(row.get("My Rating", "0")) or 0)
            rating = r if 1 <= r <= 5 else None
        except ValueError:
            pass

        year = None
        for col in ("Original Publication Year", "Year Published"):
            y = _clean(row.get(col, ""))
            if y.isdigit():
                year = int(y)
                break
        if year is None and meta:
            year = meta.get("year")

        description = (meta or {}).get("description", "")

        cover_path = None
        if meta and meta.get("cover_url"):
            data = download_cover(meta["cover_url"])
            if data:
                cover_path = save_cover_file(data, ".jpg")

        read_at = _parse_date(row.get("Date Read", ""))
        is_read = exclusive_shelf == "read" or read_at is not None

        book = Book(
            title=title, author_id=author.id, shelf_id=shelf.id, user_id=user.id,
            description=description, published_year=year, cover_path=cover_path,
            file_path="", file_format="", file_size=0,
            is_read=is_read, read_at=read_at, rating=rating,
            in_reading_list=(exclusive_shelf == "to-read"),
        )
        db.add(book)
        existing.add(key)
        imported += 1

    db.commit()
    return {"imported": imported, "skipped": skipped, "total": total}
