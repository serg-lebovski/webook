"""Получение метаданных книги по ISBN через Open Library (stdlib, без зависимостей)."""
import json
import re
import urllib.request

_TIMEOUT = 12
_UA = {"User-Agent": "WeBook/1.0"}


def normalize_isbn(raw: str) -> str:
    return re.sub(r"[^0-9Xx]", "", raw or "").upper()


def _get_json(url: str):
    req = urllib.request.Request(url, headers=_UA)
    with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
        return json.loads(resp.read().decode("utf-8"))


def fetch_by_isbn(isbn: str) -> dict | None:
    """Возвращает {title, author, year, description, publisher, cover_url} или None."""
    isbn = normalize_isbn(isbn)
    if len(isbn) not in (10, 13):
        return None
    try:
        data = _get_json(
            f"https://openlibrary.org/api/books?bibkeys=ISBN:{isbn}&format=json&jscmd=data"
        )
    except Exception:
        return None
    rec = data.get(f"ISBN:{isbn}")
    if not rec:
        return None
    authors = ", ".join(a.get("name", "") for a in rec.get("authors", []) if a.get("name"))
    year = None
    m = re.search(r"\d{4}", rec.get("publish_date", "") or "")
    if m:
        year = int(m.group())
    cover_url = (rec.get("cover") or {}).get("large") or (rec.get("cover") or {}).get("medium")
    desc = rec.get("notes") or ""
    if isinstance(desc, dict):
        desc = desc.get("value", "")
    return {
        "isbn": isbn,
        "title": rec.get("title", "").strip(),
        "author": authors.strip(),
        "year": year,
        "description": (desc or "").strip(),
        "publisher": ", ".join(p.get("name", "") for p in rec.get("publishers", []) if p.get("name")),
        "cover_url": cover_url,
    }


def download_cover(url: str) -> bytes | None:
    if not url:
        return None
    try:
        req = urllib.request.Request(url, headers=_UA)
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
            data = resp.read()
        return data if data and len(data) > 100 else None
    except Exception:
        return None
