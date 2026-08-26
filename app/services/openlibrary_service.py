"""Обогащение авторов данными из Open Library (без ключа, best-effort)."""
import json
import re
import urllib.parse
import urllib.request

_TIMEOUT = 12
_UA = {"User-Agent": "WeBook/1.0"}


def _get_json(url: str):
    req = urllib.request.Request(url, headers=_UA)
    with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
        return json.loads(resp.read().decode("utf-8"))


def fetch_author_info(name: str) -> dict | None:
    """Возвращает {name, bio, birth_year, photo_url} или None, если не нашли/сеть недоступна."""
    name = (name or "").strip()
    if not name:
        return None
    try:
        data = _get_json(f"https://openlibrary.org/search/authors.json?q={urllib.parse.quote(name)}")
    except Exception:
        return None

    docs = data.get("docs") or []
    if not docs:
        return None
    doc = docs[0]
    olid = doc.get("key")
    if not olid:
        return None

    bio = ""
    try:
        detail = _get_json(f"https://openlibrary.org/authors/{olid}.json")
        b = detail.get("bio")
        if isinstance(b, dict):
            bio = b.get("value", "")
        elif isinstance(b, str):
            bio = b
    except Exception:
        pass

    birth_year = None
    m = re.search(r"\d{4}", doc.get("birth_date", "") or "")
    if m:
        birth_year = int(m.group())

    return {
        "name": doc.get("name") or name,
        "bio": (bio or "").strip(),
        "birth_year": birth_year,
        "photo_url": f"https://covers.openlibrary.org/a/olid/{olid}-L.jpg",
    }
