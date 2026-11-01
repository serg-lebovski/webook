"""Оглавление и «объём» книги для страницы книги (вкладки «Оглавление» / «Статистика»).

Считается лениво (JSON-запросом со страницы), кэшируется в памяти процесса по
(book_id, file_size, mtime) — разбор большого EPUB/PDF занимает до секунды.

`goto` у пунктов оглавления понимает читалка (`/books/{id}/read?goto=…`):
  * EPUB — href главы (epub.js `rendition.display(href)`);
  * FB2  — `t<N>`: N-й заголовок `.fb2-title` в HTML из `convert_fb2_to_html`
           (порядок заголовков в XML основного <body> совпадает с порядком в HTML);
  * PDF  — `p<N>`: страница (iframe `#page=N`).
"""
from __future__ import annotations

import io
import threading
from collections import OrderedDict

from app.config import BOOKS_DIR

CHARS_PER_AUTHOR_SHEET = 40000      # авторский лист
CHARS_PER_MINUTE = 1300             # средняя скорость чтения «про себя»
_MAX_TOC = 400

_cache: "OrderedDict[tuple, dict]" = OrderedDict()
_lock = threading.Lock()


def _epub_toc(data: bytes) -> list:
    from ebooklib import epub
    from app.services.book_service import _epub_tmp_path
    with _epub_tmp_path(data) as path:
        book = epub.read_epub(path)
    out = []

    def walk(items, level):
        for it in items:
            if len(out) >= _MAX_TOC:
                return
            if isinstance(it, tuple) and len(it) == 2:
                sec, children = it
                title = getattr(sec, "title", "") or ""
                href = getattr(sec, "href", "") or ""
                if title:
                    out.append({"title": title.strip(), "level": level, "goto": href})
                walk(children, level + 1)
            else:
                title = getattr(it, "title", "") or ""
                href = getattr(it, "href", "") or ""
                if title:
                    out.append({"title": title.strip(), "level": level, "goto": href})

    walk(book.toc or [], 0)
    return out


def _fb2_toc(data: bytes) -> list:
    from app.services.book_service import _fb2_parse, FB2_NS
    root = _fb2_parse(data)
    bodies = root.findall(f"{{{FB2_NS}}}body")
    main = next((b for b in bodies if b.get("name") not in ("notes", "comments", "footnotes")), None)
    if main is None:
        return []
    out = []
    idx = 0
    title_tag = f"{{{FB2_NS}}}title"
    section_tag = f"{{{FB2_NS}}}section"
    for el in main.iter(title_tag):
        text = " ".join(" ".join(el.itertext()).split())
        level = sum(1 for a in el.iterancestors() if a.tag == section_tag)
        if text and len(out) < _MAX_TOC:
            out.append({"title": text[:200], "level": max(0, level - 1), "goto": f"t{idx}"})
        idx += 1
    return out


def _pdf_toc(data: bytes) -> list:
    from pypdf import PdfReader
    reader = PdfReader(io.BytesIO(data))
    out = []

    def walk(items, level):
        for it in items:
            if len(out) >= _MAX_TOC:
                return
            if isinstance(it, list):
                walk(it, level + 1)
                continue
            try:
                page = reader.get_destination_page_number(it) + 1
            except Exception:
                page = None
            title = (getattr(it, "title", "") or "").strip()
            if title:
                out.append({"title": title, "level": level, "goto": f"p{page}" if page else ""})

    try:
        walk(reader.outline or [], 0)
    except Exception:
        pass
    return out


def _compute(data: bytes, fmt: str) -> dict:
    from app.services.book_service import extract_book_text
    toc: list = []
    try:
        if fmt == "epub":
            toc = _epub_toc(data)
        elif fmt == "fb2":
            toc = _fb2_toc(data)
        elif fmt == "pdf":
            toc = _pdf_toc(data)
    except Exception:
        toc = []
    try:
        paragraphs = extract_book_text(data, "." + fmt) or []
    except Exception:
        paragraphs = []
    chars = sum(len(p) for p in paragraphs)
    words = sum(len(p.split()) for p in paragraphs)
    return {
        "toc": toc,
        "chars": chars,
        "words": words,
        "paragraphs": len(paragraphs),
        "author_sheets": round(chars / CHARS_PER_AUTHOR_SHEET, 2) if chars else 0,
        "minutes": round(chars / CHARS_PER_MINUTE) if chars else 0,
        "pages": round(chars / 1800) if chars else 0,   # «условная» страница ~1800 зн.
    }


def insights(book) -> dict:
    if not book.file_path or book.file_format not in ("epub", "fb2", "pdf"):
        return {"toc": [], "chars": 0, "words": 0, "paragraphs": 0, "author_sheets": 0, "minutes": 0, "pages": 0}
    path = BOOKS_DIR / book.file_path
    try:
        st = path.stat()
    except OSError:
        return {"toc": [], "chars": 0, "words": 0, "paragraphs": 0, "author_sheets": 0, "minutes": 0, "pages": 0,
                "error": "Файл книги не найден"}
    key = (book.id, st.st_size, int(st.st_mtime))
    with _lock:
        if key in _cache:
            _cache.move_to_end(key)
            return _cache[key]
    res = _compute(path.read_bytes(), book.file_format)
    with _lock:
        _cache[key] = res
        while len(_cache) > 300:
            _cache.popitem(last=False)
    return res
