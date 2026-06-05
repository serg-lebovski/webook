"""Экспорт/импорт библиотеки пользователя (с manifest.json для восстановления),
дамп БД и полный серверный архив для администратора."""
import json
import os
import re
import shutil
import subprocess
import tempfile
import urllib.parse
import zipfile
from datetime import datetime
from pathlib import Path

from app.config import (
    BOOKS_DIR, COVERS_DIR, LINKS_CONTENT_DIR, AUDIOBOOKS_DIR, DATABASE_URL,
)
from app.models.book import Book
from app.models.link import Link, LinkFolder
from app.models.shelf import Shelf
from app.models.author import Author
from app.models.series import Series
from app.models.highlight import Highlight
from app.models.audiobook import Audiobook, AudiobookTrack
from app.services.book_service import save_book_file, save_cover_file
from app.services.tag_service import get_or_create_tags
from app.services import audiobook_service

_DEFLATE_EXT = {".fb2", ".html", ".htm", ".txt", ".epub"}


def _comp(ext: str) -> int:
    return zipfile.ZIP_DEFLATED if ext.lower() in _DEFLATE_EXT else zipfile.ZIP_STORED


def _safe_filename(s: str, max_len: int = 180) -> str:
    return re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", s or "").strip(" .")[:max_len] or "file"


def _unique_name(name: str, used: set) -> str:
    c = name
    i = 2
    while c in used:
        base, dot, ext = name.rpartition(".")
        c = f"{base}_{i}.{ext}" if dot else f"{name}_{i}"
        i += 1
    used.add(c)
    return c


def _ext(fmt: str) -> str:
    return fmt if fmt.startswith(".") else "." + fmt


# ─────────────────────────── get-or-create ───────────────────────────

def _goc_author(db, name, bio=""):
    name = (name or "").strip() or "Неизвестный автор"
    a = db.query(Author).filter(Author.name.ilike(name)).first()
    if not a:
        a = Author(name=name, bio=bio or "")
        db.add(a); db.flush()
    return a


def _goc_shelf(db, user, name, desc=""):
    name = (name or "").strip() or "Без полки"
    s = db.query(Shelf).filter(Shelf.user_id == user.id, Shelf.name.ilike(name)).first()
    if not s:
        s = Shelf(name=name, user_id=user.id, description=desc or "")
        db.add(s); db.flush()
    return s


def _goc_series(db, name, author_id):
    name = (name or "").strip()
    if not name:
        return None
    s = db.query(Series).filter(Series.author_id == author_id, Series.name.ilike(name)).first()
    if not s:
        s = Series(name=name, author_id=author_id)
        db.add(s); db.flush()
    return s


def _goc_folder(db, user, name):
    name = (name or "").strip()
    if not name:
        return None
    f = db.query(LinkFolder).filter(LinkFolder.user_id == user.id, LinkFolder.name.ilike(name)).first()
    if not f:
        f = LinkFolder(name=name, user_id=user.id)
        db.add(f); db.flush()
    return f


# ─────────────────────────── export ───────────────────────────

def export_user(user, db, zip_path: str):
    """Архив библиотеки пользователя: человекочитаемые файлы + manifest.json."""
    manifest = {
        "format": "webook-user", "version": 1,
        "exported_at": datetime.utcnow().isoformat() + "Z", "username": user.username,
        "shelves": [], "authors": [], "series": [], "link_folders": [],
        "books": [], "links": [], "audiobooks": [],
    }

    hl = {}
    for h in db.query(Highlight).filter(Highlight.user_id == user.id).all():
        hl.setdefault((h.resource_type, h.resource_id), []).append(
            {"quote": h.quote, "note": h.note or "", "location": h.location, "color": h.color})

    for s in db.query(Shelf).filter(Shelf.user_id == user.id).all():
        manifest["shelves"].append({"name": s.name, "description": s.description or ""})
    for f in db.query(LinkFolder).filter(LinkFolder.user_id == user.id).all():
        manifest["link_folders"].append({"name": f.name})

    authors_seen, series_seen = {}, set()

    with zipfile.ZipFile(zip_path, "w", allowZip64=True) as zf:
        used_books, used_covers, used_articles, used_ab = set(), set(), set(), set()

        for b in db.query(Book).filter(Book.user_id == user.id,
                                        Book.deleted_at.is_(None)).all():
            # книга может быть бесфайловой (карточка «Хочу прочитать» по ISBN)
            fp = BOOKS_DIR / b.file_path if b.file_path else None
            has_file = bool(fp and fp.is_file())
            author = b.author.name if b.author else "Неизвестный автор"
            authors_seen.setdefault(author, (b.author.bio if b.author else "") or "")
            base = _safe_filename(f"{author} - {b.title}")
            file_arc = None
            if has_file:
                fname = _unique_name(base + _ext(b.file_format), used_books)
                zf.write(fp, f"books/{fname}", compress_type=_comp(fp.suffix))
                file_arc = f"books/{fname}"
            cover_arc = None
            if b.cover_path and (COVERS_DIR / b.cover_path).exists():
                cn = _unique_name(base + ".jpg", used_covers)
                zf.write(COVERS_DIR / b.cover_path, f"covers/{cn}", compress_type=zipfile.ZIP_STORED)
                cover_arc = f"covers/{cn}"
            if b.series:
                series_seen.add((b.series.name, author))
            manifest["books"].append({
                "title": b.title, "author": author, "shelf": b.shelf.name if b.shelf else None,
                "series": b.series.name if b.series else None, "series_order": b.series_order,
                "description": b.description or "", "language": b.language or "",
                "published_year": b.published_year, "is_read": b.is_read,
                "rating": b.rating, "is_favorite": b.is_favorite,
                "in_reading_list": b.in_reading_list,
                "file_format": b.file_format or "",
                "tags": [t.name for t in b.tags], "file": file_arc, "cover": cover_arc,
                "highlights": hl.get(("book", b.id), []),
            })

        for name, bio in authors_seen.items():
            manifest["authors"].append({"name": name, "bio": bio})
        for sname, aname in series_seen:
            manifest["series"].append({"name": sname, "author": aname})

        for l in db.query(Link).filter(Link.user_id == user.id,
                                       Link.deleted_at.is_(None)).all():
            content = l.content
            content_arc = None
            if content:
                an = _unique_name(_safe_filename(l.title or f"article_{l.id}") + ".txt", used_articles)
                zf.writestr(f"articles/{an}", content, compress_type=zipfile.ZIP_DEFLATED)
                content_arc = f"articles/{an}"
            manifest["links"].append({
                "url": l.url, "title": l.title, "description": l.description or "",
                "folder": l.folder.name if l.folder else None, "is_read": l.is_read,
                "word_count": l.word_count or 0, "read_progress": l.read_progress or 0,
                "tags": [t.name for t in l.tags], "content_file": content_arc,
                "highlights": hl.get(("link", l.id), []),
            })

        for ab in db.query(Audiobook).filter(Audiobook.user_id == user.id,
                                             Audiobook.deleted_at.is_(None)).all():
            folder = AUDIOBOOKS_DIR / ab.folder
            base = f"{ab.author} - {ab.title}" if ab.author else ab.title
            ab_dir = _unique_name(_safe_filename(base), used_ab)
            tracks, cur_order = [], None
            for idx, t in enumerate(sorted(ab.tracks, key=lambda x: x.order), start=1):
                src = folder / t.filename
                if not src.exists():
                    continue
                ext = src.suffix or ("." + (t.file_format or "mp3"))
                arc = f"audiobooks/{ab_dir}/{idx:02d}{ext}"
                zf.write(src, arc, compress_type=zipfile.ZIP_STORED)
                if t.id == ab.current_track_id:
                    cur_order = t.order
                tracks.append({"file": arc, "title": t.title or "", "order": t.order,
                               "duration": t.duration, "file_format": t.file_format or ext.lstrip(".")})
            cover_arc = None
            if ab.cover_path and (COVERS_DIR / ab.cover_path).exists():
                zf.write(COVERS_DIR / ab.cover_path, f"audiobooks/{ab_dir}/cover.jpg", compress_type=zipfile.ZIP_STORED)
                cover_arc = f"audiobooks/{ab_dir}/cover.jpg"
            manifest["audiobooks"].append({
                "title": ab.title, "author": ab.author or "", "narrator": ab.narrator or "",
                "description": ab.description or "", "is_finished": ab.is_finished,
                "position": ab.position or 0, "current_order": cur_order,
                "cover": cover_arc, "tracks": tracks,
            })

        zf.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=1))


# ─────────────────────────── import ───────────────────────────

def import_user(user, db, zip_path: str) -> dict:
    """Восстанавливает библиотеку пользователя из его архива. Точные дубли
    (та же книга/ссылка/аудиокнига) пропускаются."""
    counts = {"books": 0, "links": 0, "audiobooks": 0, "skipped": 0}
    with zipfile.ZipFile(zip_path) as zf:
        names = set(zf.namelist())
        if "manifest.json" not in names:
            raise ValueError("В архиве нет manifest.json — это не архив WeBook для восстановления.")
        manifest = json.loads(zf.read("manifest.json").decode("utf-8"))
        if manifest.get("format") != "webook-user":
            raise ValueError("Неподдерживаемый формат архива.")

        shelf_map = {s["name"]: _goc_shelf(db, user, s["name"], s.get("description", ""))
                     for s in manifest.get("shelves", [])}
        author_map = {a["name"]: _goc_author(db, a["name"], a.get("bio", ""))
                      for a in manifest.get("authors", [])}
        series_map = {}
        for s in manifest.get("series", []):
            au = author_map.get(s["author"]) or _goc_author(db, s["author"], "")
            series_map[(s["name"], s["author"])] = _goc_series(db, s["name"], au.id)
        folder_map = {f["name"]: _goc_folder(db, user, f["name"]) for f in manifest.get("link_folders", [])}
        db.flush()

        for bm in manifest.get("books", []):
            has_file = bm.get("file") in names
            author = author_map.get(bm.get("author")) or _goc_author(db, bm.get("author") or "", "")
            data = zf.read(bm["file"]) if has_file else b""
            # дедуп: с файлом — по размеру, бесфайловые — по названию+автору
            dup = db.query(Book).filter(Book.user_id == user.id, Book.title == bm["title"],
                                        Book.author_id == author.id)
            dup = dup.filter(Book.file_size == len(data)) if has_file else dup.filter(Book.file_path == "")
            if dup.first():
                counts["skipped"] += 1
                continue
            shelf = shelf_map.get(bm.get("shelf")) or _goc_shelf(db, user, bm.get("shelf") or "", "")
            series = series_map.get((bm.get("series"), bm.get("author"))) if bm.get("series") else None
            if bm.get("series") and not series:
                series = _goc_series(db, bm["series"], author.id)
            ext = Path(bm["file"]).suffix if has_file else ""
            fname = save_book_file(data, ext) if has_file else ""
            cover_path = None
            if bm.get("cover") in names:
                cover_path = save_cover_file(zf.read(bm["cover"]), ".jpg")
            book = Book(
                user_id=user.id, title=bm["title"], author_id=author.id, shelf_id=shelf.id,
                series_id=series.id if series else None, series_order=bm.get("series_order"),
                description=bm.get("description", ""), language=bm.get("language", ""),
                published_year=bm.get("published_year"), is_read=bool(bm.get("is_read")),
                rating=bm.get("rating"), is_favorite=bool(bm.get("is_favorite")),
                in_reading_list=bool(bm.get("in_reading_list")) or not has_file,
                cover_path=cover_path, file_path=fname,
                file_format=ext.lstrip(".") if has_file else "", file_size=len(data),
            )
            if bm.get("tags"):
                book.tags = get_or_create_tags(bm["tags"], user.id, db)
            db.add(book); db.flush()
            for h in bm.get("highlights", []):
                db.add(Highlight(user_id=user.id, resource_type="book", resource_id=book.id,
                                 quote=h.get("quote", ""), note=h.get("note", ""),
                                 location=h.get("location"), color=h.get("color", "yellow")))
            counts["books"] += 1
        db.commit()

        for lm in manifest.get("links", []):
            if db.query(Link).filter(Link.user_id == user.id, Link.url == lm["url"]).first():
                counts["skipped"] += 1
                continue
            folder = folder_map.get(lm.get("folder")) if lm.get("folder") else None
            if lm.get("folder") and not folder:
                folder = _goc_folder(db, user, lm["folder"])
            link = Link(
                user_id=user.id, url=lm["url"], title=lm.get("title") or lm["url"],
                description=lm.get("description", ""), is_read=bool(lm.get("is_read")),
                folder_id=folder.id if folder else None,
                word_count=lm.get("word_count", 0), read_progress=lm.get("read_progress", 0),
            )
            if lm.get("tags"):
                link.tags = get_or_create_tags(lm["tags"], user.id, db)
            db.add(link); db.flush()
            if lm.get("content_file") in names:
                content = zf.read(lm["content_file"]).decode("utf-8")
                LINKS_CONTENT_DIR.mkdir(parents=True, exist_ok=True)
                (LINKS_CONTENT_DIR / f"{link.id}.txt").write_text(content, encoding="utf-8")
            for h in lm.get("highlights", []):
                db.add(Highlight(user_id=user.id, resource_type="link", resource_id=link.id,
                                 quote=h.get("quote", ""), note=h.get("note", ""),
                                 location=h.get("location"), color=h.get("color", "yellow")))
            counts["links"] += 1
        db.commit()

        for am in manifest.get("audiobooks", []):
            if db.query(Audiobook).filter(Audiobook.user_id == user.id, Audiobook.title == am["title"],
                                          Audiobook.author == (am.get("author") or "")).first():
                counts["skipped"] += 1
                continue
            folder = audiobook_service.new_folder()
            ab = Audiobook(
                user_id=user.id, title=am["title"], author=am.get("author", ""),
                narrator=am.get("narrator", ""), description=am.get("description", ""),
                folder=folder, is_finished=bool(am.get("is_finished")), position=am.get("position", 0),
            )
            if am.get("cover") in names:
                ab.cover_path = save_cover_file(zf.read(am["cover"]), ".jpg")
            db.add(ab); db.flush()
            cur_track_id = None
            for tm in am.get("tracks", []):
                if tm.get("file") not in names:
                    continue
                tdata = zf.read(tm["file"])
                stored = audiobook_service.safe_track_name(tm["file"])
                (audiobook_service.book_dir(folder) / stored).write_bytes(tdata)
                tr = AudiobookTrack(audiobook_id=ab.id, filename=stored, title=tm.get("title", ""),
                                    order=tm.get("order", 0), duration=tm.get("duration"),
                                    file_format=tm.get("file_format", ""), file_size=len(tdata))
                db.add(tr); db.flush()
                if am.get("current_order") is not None and tm.get("order") == am.get("current_order"):
                    cur_track_id = tr.id
            ab.current_track_id = cur_track_id
            counts["audiobooks"] += 1
        db.commit()

    return counts


# ─────────────────────────── admin: DB + full ───────────────────────────

def db_dump(dest_path: str) -> str:
    """pg_dump в dest_path (PostgreSQL) или копия файла (SQLite). Возвращает тип."""
    if DATABASE_URL.startswith("postgresql"):
        u = urllib.parse.urlparse(DATABASE_URL)
        env = dict(os.environ)
        if u.password:
            env["PGPASSWORD"] = urllib.parse.unquote(u.password)
        cmd = [
            "pg_dump", "--no-owner", "--no-privileges",
            "-h", u.hostname or "localhost", "-p", str(u.port or 5432),
            "-U", urllib.parse.unquote(u.username or "postgres"),
            (u.path or "/").lstrip("/") or "postgres",
        ]
        with open(dest_path, "wb") as f:
            subprocess.run(cmd, env=env, stdout=f, check=True)
        return "postgresql"
    # SQLite
    src = DATABASE_URL.split("///")[-1] if "///" in DATABASE_URL else DATABASE_URL.split("//")[-1]
    shutil.copyfile(src, dest_path)
    return "sqlite"


_FULL_RESTORE_README = """WeBook — полный архив сервера ({kind})

Содержимое:
  {db}        — дамп базы данных
  books/      — файлы книг
  files/      — обложки и изображения
  audiobooks/ — аудиокниги
  links/      — тексты статей

ВОССТАНОВЛЕНИЕ на чистом сервере WeBook:
  1) Распакуйте books/ files/ audiobooks/ links/ в каталог проекта
     (рядом с docker-compose.yml — это смонтированные тома).
  2) Поднимите контейнеры:  docker compose up --build -d
  3) Восстановите БД:
       PostgreSQL: gunzip/cat {db} | docker exec -i webook-db psql -U webook webook
       SQLite:     замените файл webook.db на {db}
  4) Перезапустите app:  docker compose restart app
"""


def full_export(db, zip_path: str):
    """Полный серверный архив: дамп БД + все файловые каталоги."""
    fd, sql_path = tempfile.mkstemp(prefix="webook_db_", suffix=".sql")
    os.close(fd)
    try:
        kind = db_dump(sql_path)
        db_arc = "database.sql" if kind == "postgresql" else "database.sqlite"
        with zipfile.ZipFile(zip_path, "w", allowZip64=True) as zf:
            zf.write(sql_path, db_arc, compress_type=zipfile.ZIP_DEFLATED)
            for d, arc in [(BOOKS_DIR, "books"), (COVERS_DIR, "files"),
                           (AUDIOBOOKS_DIR, "audiobooks"), (LINKS_CONTENT_DIR, "links")]:
                if not d.exists():
                    continue
                for f in d.rglob("*"):
                    if f.is_file():
                        zf.write(f, f"{arc}/{f.relative_to(d).as_posix()}", compress_type=zipfile.ZIP_STORED)
            zf.writestr("RESTORE.txt", _FULL_RESTORE_README.format(db=db_arc, kind=kind))
    finally:
        if os.path.exists(sql_path):
            os.unlink(sql_path)
