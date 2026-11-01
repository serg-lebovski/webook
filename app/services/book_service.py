"""Parse metadata and covers from epub/fb2/pdf files."""
import io
import os
import tempfile
import uuid
from contextlib import contextmanager
from html import escape
from pathlib import Path
from typing import Optional

from app.config import BOOKS_DIR, COVERS_DIR


@contextmanager
def _epub_tmp_path(data: bytes):
    """ebooklib 0.18's read_epub() only accepts a real path/directory (it calls
    os.path.isdir() on it unconditionally) — it cannot read from a BytesIO, so
    in-memory bytes must be spooled to a temp file first."""
    fd, path = tempfile.mkstemp(suffix=".epub")
    os.close(fd)
    try:
        with open(path, "wb") as f:
            f.write(data)
        yield path
    finally:
        os.unlink(path)


def save_upload(data: bytes, suffix: str, dest_dir: Path) -> str:
    dest_dir.mkdir(parents=True, exist_ok=True)
    name = f"{uuid.uuid4().hex}{suffix}"
    (dest_dir / name).write_bytes(data)
    return name


def _empty_meta() -> dict:
    return {
        "title": "", "author": "", "description": "", "language": "", "cover_data": None,
        "series": "", "series_order": None, "year": None, "genres": [], "keywords": [],
    }


def _to_order(v) -> Optional[float]:
    try:
        f = float(str(v).strip().replace(",", "."))
    except (TypeError, ValueError):
        return None
    return f if 0 < f < 100000 else None


def _year_of(v) -> Optional[int]:
    import re as _re
    m = _re.search(r"(1[5-9]\d\d|20\d\d)", str(v or ""))
    return int(m.group(1)) if m else None


def _strip_html(s: str) -> str:
    """Описание в EPUB часто приходит HTML-фрагментом — оставляем текст с абзацами."""
    if "<" not in s:
        return s.strip()
    try:
        import re as _re
        from lxml import html as lhtml
        doc = lhtml.fromstring(s.encode("utf-8"), parser=lhtml.HTMLParser(encoding="utf-8"))
        for br in doc.iter("br"):
            br.tail = "\n" + (br.tail or "")
        for p in doc.iter("p", "div"):
            p.tail = "\n\n" + (p.tail or "")
        return _re.sub(r"\n{3,}", "\n\n", doc.text_content()).strip()
    except Exception:
        return s.strip()


def extract_epub(data: bytes) -> dict:
    try:
        import ebooklib
        from ebooklib import epub

        with _epub_tmp_path(data) as path:
            book = epub.read_epub(path)
        meta = _empty_meta()
        title = book.get_metadata("DC", "title")
        if title:
            meta["title"] = (title[0][0] or "").strip()
        creator = book.get_metadata("DC", "creator")
        if creator:
            meta["author"] = (creator[0][0] or "").strip()
        desc = book.get_metadata("DC", "description")
        if desc:
            meta["description"] = _strip_html(desc[0][0] or "")
        lang = book.get_metadata("DC", "language")
        if lang:
            meta["language"] = (lang[0][0] or "").strip()
        date = book.get_metadata("DC", "date")
        if date:
            meta["year"] = _year_of(date[0][0])
        for subj in book.get_metadata("DC", "subject") or []:
            s = (subj[0] or "").strip()
            if s and s not in meta["genres"]:
                meta["genres"].append(s)

        # Цикл: calibre (<meta name="calibre:series" content=…>) или EPUB3
        # (<meta property="belongs-to-collection" id=…> + refines group-position).
        # ebooklib раскладывает <meta name="calibre:series"> в metadata["calibre"]["series"]
        cal = book.metadata.get("calibre", {}) if hasattr(book, "metadata") else {}
        for _v, attrs in cal.get("series", []):
            if (attrs or {}).get("content"):
                meta["series"] = attrs["content"].strip()
        for _v, attrs in cal.get("series_index", []):
            if (attrs or {}).get("content"):
                meta["series_order"] = _to_order(attrs["content"])
        coll_ids = {}
        for value, attrs in book.get_metadata("OPF", None) or []:
            attrs = attrs or {}
            name = attrs.get("name", "")
            prop = attrs.get("property", "")
            if name == "calibre:series" and attrs.get("content"):
                meta["series"] = attrs["content"].strip()
            elif name == "calibre:series_index" and attrs.get("content"):
                meta["series_order"] = _to_order(attrs["content"])
            elif prop == "belongs-to-collection" and value:
                coll_ids[attrs.get("id", "")] = value.strip()
                if not meta["series"]:
                    meta["series"] = value.strip()
            elif prop == "group-position" and value and meta["series_order"] is None:
                ref = (attrs.get("refines") or "").lstrip("#")
                if not coll_ids or ref in coll_ids:
                    meta["series_order"] = _to_order(value)

        for item in book.get_items():
            if item.get_type() == ebooklib.ITEM_COVER:
                meta["cover_data"] = item.get_content()
                break
        if not meta["cover_data"]:
            for item in book.get_items_of_type(ebooklib.ITEM_IMAGE):
                if "cover" in item.get_name().lower():
                    meta["cover_data"] = item.get_content()
                    break

        return meta
    except Exception:
        return {}


FB2_NS = "http://www.gribuser.ru/xml/fictionbook/2.0"


def _fb2_parse(data: bytes):
    from lxml import etree
    parser = etree.XMLParser(recover=True, huge_tree=True)
    return etree.fromstring(data, parser=parser)


def _fb2_text(el) -> str:
    if el is None:
        return ""
    from lxml import etree
    return " ".join(etree.tostring(el, method="text", encoding="unicode").split())


def _fb2_author(author_el) -> str:
    if author_el is None:
        return ""
    ns = {"fb": FB2_NS}
    parts = []
    for tag in ("first-name", "last-name"):
        el = author_el.find(f"fb:{tag}", ns)
        if el is not None and el.text and el.text.strip():
            parts.append(el.text.strip())
    if not parts:
        nick = author_el.find("fb:nickname", ns)
        if nick is not None and nick.text:
            parts.append(nick.text.strip())
    return " ".join(parts)


def extract_fb2(data: bytes) -> dict:
    try:
        ns = {"fb": FB2_NS, "l": "http://www.w3.org/1999/xlink"}
        root = _fb2_parse(data)
        meta = _empty_meta()

        ti = root.find(".//fb:description/fb:title-info", ns)
        if ti is None:
            ti = root
        pi = root.find(".//fb:description/fb:publish-info", ns)

        meta["title"] = _fb2_text(ti.find("fb:book-title", ns))
        authors = [a for a in (_fb2_author(x) for x in ti.findall("fb:author", ns)) if a]
        meta["author"] = authors[0] if authors else ""

        ann = ti.find("fb:annotation", ns)
        if ann is not None:
            paras = [_fb2_text(p) for p in ann.iter(f"{{{FB2_NS}}}p")]
            paras = [p for p in paras if p]
            meta["description"] = "\n\n".join(paras) if paras else _fb2_text(ann)

        lang = ti.find("fb:lang", ns)
        if lang is not None and lang.text:
            meta["language"] = lang.text.strip()

        date = ti.find("fb:date", ns)
        if date is not None:
            meta["year"] = _year_of(date.get("value") or date.text)
        if not meta["year"] and pi is not None:
            meta["year"] = _year_of(_fb2_text(pi.find("fb:year", ns)))

        for g in ti.findall("fb:genre", ns):
            code = (g.text or "").strip()
            if code and code not in meta["genres"]:
                meta["genres"].append(code)
        kw = _fb2_text(ti.find("fb:keywords", ns))
        if kw:
            meta["keywords"] = [k.strip() for k in kw.replace(";", ",").split(",") if k.strip()]

        # Цикл: <sequence name="…" number="N"/> (бывает вложенный — берём верхний)
        seq = ti.find("fb:sequence", ns)
        if seq is None and pi is not None:
            seq = pi.find("fb:sequence", ns)
        if seq is not None and seq.get("name"):
            meta["series"] = seq.get("name").strip()
            meta["series_order"] = _to_order(seq.get("number"))

        cover_page = ti.find("fb:coverpage", ns)
        if cover_page is not None:
            img = cover_page.find("fb:image", ns)
            if img is not None:
                href = img.get("{http://www.w3.org/1999/xlink}href", "")
                if href.startswith("#"):
                    bid = href[1:]
                    for binary in root.findall(".//fb:binary", ns):
                        if binary.get("id") == bid:
                            import base64
                            meta["cover_data"] = base64.b64decode(binary.text or "")
                            break
        return meta
    except Exception:
        return {}


def extract_pdf(data: bytes) -> dict:
    try:
        from pypdf import PdfReader

        reader = PdfReader(io.BytesIO(data))
        info = reader.metadata or {}
        meta = _empty_meta()
        meta.update({
            "title": str(info.get("/Title", "") or "").strip(),
            "author": str(info.get("/Author", "") or "").strip(),
            "description": str(info.get("/Subject", "") or "").strip(),
            "year": _year_of(info.get("/CreationDate", "")),
        })
        return meta
    except Exception:
        return {}


# ─────────────────────────── архивы ───────────────────────────

ARCHIVE_SUFFIXES = (".zip",)
_MAX_ARCHIVE_ENTRIES = 2000
_MAX_ARCHIVE_TOTAL = 3 * 1024 * 1024 * 1024   # защита от zip-бомб: ≤3 ГБ распакованного


def is_archive_name(name: str) -> bool:
    return (name or "").lower().endswith(ARCHIVE_SUFFIXES)


def _zip_entry_name(info) -> str:
    """Имена в ZIP из Windows-архиваторов без UTF-8 флага лежат в cp866, а zipfile
    декодирует их как cp437 → кракозябры. Перекодируем."""
    name = info.filename
    if not (info.flag_bits & 0x800):
        try:
            name = name.encode("cp437").decode("cp866")
        except (UnicodeEncodeError, UnicodeDecodeError):
            pass
    return name


def expand_archive(fileobj, allowed_suffixes, max_file_size: int, _depth: int = 0) -> tuple:
    """Распаковать ZIP (в т.ч. «book.fb2.zip» и архив из таких архивов — 1 уровень
    вложенности) в список (имя_файла, bytes) поддерживаемых книг.
    Возвращает (items, errors). EPUB — тоже zip, но он сам книга: его не вызываем."""
    import zipfile
    items, errors = [], []
    try:
        zf = zipfile.ZipFile(fileobj)
    except zipfile.BadZipFile:
        return [], ["файл повреждён или это не ZIP-архив"]
    total = 0
    with zf:
        infos = [i for i in zf.infolist() if not i.is_dir()]
        if len(infos) > _MAX_ARCHIVE_ENTRIES:
            return [], [f"в архиве слишком много файлов (>{_MAX_ARCHIVE_ENTRIES})"]
        for info in infos:
            name = _zip_entry_name(info)
            base = name.replace("\\", "/").rsplit("/", 1)[-1]
            if not base or base.startswith(".") or "__MACOSX" in name:
                continue
            suffix = Path(base.lower()).suffix
            nested = suffix in ARCHIVE_SUFFIXES
            if not nested and suffix not in allowed_suffixes:
                continue
            limit = max_file_size * 5 if nested else max_file_size
            if info.file_size > limit:
                errors.append(f"{base}: файл больше {limit // (1024 * 1024)} МБ")
                continue
            total += info.file_size
            if total > _MAX_ARCHIVE_TOTAL:
                errors.append("архив слишком большой после распаковки")
                break
            try:
                blob = zf.read(info)
            except Exception as e:  # битый/зашифрованный элемент
                errors.append(f"{base}: не удалось распаковать ({e.__class__.__name__})")
                continue
            if nested:
                if _depth >= 1:
                    continue
                sub_items, sub_err = expand_archive(io.BytesIO(blob), allowed_suffixes,
                                                    max_file_size, _depth + 1)
                items.extend(sub_items)
                errors.extend(f"{base}: {e}" for e in sub_err)
            else:
                items.append((base, blob))
    return items, errors


def parse_book_file(data: bytes, suffix: str) -> dict:
    suffix = suffix.lower()
    if suffix == ".epub":
        return extract_epub(data)
    elif suffix == ".fb2":
        return extract_fb2(data)
    elif suffix == ".pdf":
        return extract_pdf(data)
    return {}


def save_book_file(data: bytes, suffix: str) -> str:
    return save_upload(data, suffix, BOOKS_DIR)


def save_cover_file(data: bytes, suffix: str) -> Optional[str]:
    if not data:
        return None
    try:
        from PIL import Image
        img = Image.open(io.BytesIO(data))
        img = img.convert("RGB")
        img.thumbnail((400, 600))
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=85)
        return save_upload(buf.getvalue(), ".jpg", COVERS_DIR)
    except Exception:
        return None


def delete_file(rel_path: Optional[str], base_dir: Path):
    if rel_path:
        p = base_dir / rel_path
        if p.exists():
            p.unlink(missing_ok=True)


def _html_to_paragraphs(html_bytes) -> list:
    """Извлечь абзацы текста из HTML-фрагмента (для озвучки/TTS)."""
    try:
        from lxml import html as lhtml

        # ВАЖНО: без объявления кодировки libxml2 трактует HTML как latin-1 и
        # портит кириллицу. Принудительно декодируем как UTF-8. Используем именно
        # lxml.html.HTMLParser — он возвращает HtmlElement с методом text_content().
        if isinstance(html_bytes, str):
            html_bytes = html_bytes.encode("utf-8")
        parser = lhtml.HTMLParser(encoding="utf-8")
        doc = lhtml.fromstring(html_bytes, parser=parser)
        for bad in doc.xpath("//script | //style"):
            bad.drop_tree()
        parts = []
        for el in doc.iter("p", "h1", "h2", "h3", "h4", "h5", "h6", "li", "blockquote", "div"):
            # для div берём только прямой текст, чтобы не дублировать вложенные блоки
            if el.tag == "div":
                t = (el.text or "").strip()
            else:
                t = el.text_content().strip()
            if t:
                parts.append(" ".join(t.split()))
        if not parts:
            t = doc.text_content().strip()
            if t:
                parts = [" ".join(line.split()) for line in t.split("\n") if line.strip()]
        return parts
    except Exception:
        return []


def extract_epub_text(data: bytes) -> list:
    try:
        import ebooklib
        from ebooklib import epub

        with _epub_tmp_path(data) as path:
            book = epub.read_epub(path)
        docs = {
            it.get_id(): it for it in book.get_items_of_type(ebooklib.ITEM_DOCUMENT)
            if not isinstance(it, epub.EpubNav)  # оглавление, не текст книги
        }
        ordered = []
        for idref, _ in (book.spine or []):
            it = docs.pop(idref, None)
            if it is not None:
                ordered.append(it)
        ordered.extend(docs.values())  # то, чего не было в spine
        paragraphs = []
        for it in ordered:
            paragraphs.extend(_html_to_paragraphs(it.get_content()))
        return paragraphs
    except Exception:
        return []


def extract_fb2_text(data: bytes) -> list:
    html = convert_fb2_to_html(data)
    return _html_to_paragraphs(html)


def extract_pdf_text(data: bytes) -> list:
    try:
        from pypdf import PdfReader

        reader = PdfReader(io.BytesIO(data))
        paragraphs = []
        for page in reader.pages:
            text = (page.extract_text() or "").strip()
            for block in text.split("\n\n"):
                block = " ".join(block.split())
                if block:
                    paragraphs.append(block)
        return paragraphs
    except Exception:
        return []


def extract_book_text(data: bytes, suffix: str) -> list:
    """Список абзацев книги (plain text) для озвучки. Пустой список — формат не поддержан."""
    suffix = (suffix or "").lower()
    if not suffix.startswith("."):
        suffix = "." + suffix
    if suffix == ".epub":
        return extract_epub_text(data)
    if suffix == ".fb2":
        return extract_fb2_text(data)
    if suffix == ".pdf":
        return extract_pdf_text(data)
    return []


def convert_fb2_to_html(data: bytes) -> str:
    """Convert FB2 book bytes to safe HTML for in-browser reading."""
    try:
        from lxml import etree

        ns = "http://www.gribuser.ru/xml/fictionbook/2.0"
        xns = "http://www.w3.org/1999/xlink"
        root = etree.fromstring(data)

        images: dict = {}
        for binary in root.findall(f"{{{ns}}}binary"):
            bid = binary.get("id", "")
            ct = binary.get("content-type", "image/jpeg")
            if bid and binary.text:
                images[bid] = f"data:{ct};base64,{binary.text.strip()}"

        def tag_name(el) -> str:
            t = el.tag
            return t.split("}")[1] if "}" in t else t

        def inline(el) -> str:
            parts = [escape(el.text or "")]
            for child in el:
                cn = tag_name(child)
                inner = inline(child)
                if cn == "emphasis":
                    parts.append(f"<em>{inner}</em>")
                elif cn == "strong":
                    parts.append(f"<strong>{inner}</strong>")
                elif cn == "strikethrough":
                    parts.append(f"<s>{inner}</s>")
                elif cn == "sup":
                    parts.append(f"<sup>{inner}</sup>")
                elif cn == "sub":
                    parts.append(f"<sub>{inner}</sub>")
                elif cn == "code":
                    parts.append(f"<code>{inner}</code>")
                elif cn == "a":
                    href = escape(child.get(f"{{{xns}}}href", "#"))
                    parts.append(f'<a href="{href}">{inner}</a>')
                elif cn == "image":
                    href = child.get(f"{{{xns}}}href", "")
                    if href.startswith("#") and href[1:] in images:
                        parts.append(f'<img src="{images[href[1:]]}" class="fb2-image">')
                else:
                    parts.append(inner)
                parts.append(escape(child.tail or ""))
            return "".join(parts)

        def block(el) -> str:
            cn = tag_name(el)
            if cn == "section":
                return f'<div class="fb2-section">{"".join(block(c) for c in el)}</div>'
            if cn == "title":
                return f'<h3 class="fb2-title">{"".join(block(c) for c in el)}</h3>'
            if cn == "subtitle":
                return f'<h4 class="fb2-subtitle">{"".join(block(c) for c in el)}</h4>'
            if cn == "p":
                return f"<p>{inline(el)}</p>"
            if cn == "epigraph":
                return f'<blockquote class="fb2-epigraph">{"".join(block(c) for c in el)}</blockquote>'
            if cn == "cite":
                return f'<blockquote class="fb2-cite">{"".join(block(c) for c in el)}</blockquote>'
            if cn == "poem":
                return f'<div class="fb2-poem">{"".join(block(c) for c in el)}</div>'
            if cn == "stanza":
                return f'<div class="fb2-stanza">{"".join(block(c) for c in el)}</div>'
            if cn == "v":
                return f'<div class="fb2-v">{inline(el)}</div>'
            if cn == "image":
                href = el.get(f"{{{xns}}}href", "")
                if href.startswith("#") and href[1:] in images:
                    return f'<img src="{images[href[1:]]}" class="fb2-image img-fluid">'
            if cn == "empty-line":
                return "<br>"
            if cn == "text-author":
                return f'<p class="fb2-text-author text-end fst-italic">{inline(el)}</p>'
            return ""

        bodies = root.findall(f"{{{ns}}}body")
        main = next((b for b in bodies if b.get("name") not in ("notes", "comments", "footnotes")), None)
        if main is None:
            return "<p>Содержимое книги не найдено.</p>"

        return "".join(block(child) for child in main)

    except Exception as e:
        return f"<p>Ошибка чтения FB2: {escape(str(e))}</p>"
