"""Parse metadata and covers from epub/fb2/pdf files."""
import io
import uuid
from html import escape
from pathlib import Path
from typing import Optional

from app.config import BOOKS_DIR, COVERS_DIR


def save_upload(data: bytes, suffix: str, dest_dir: Path) -> str:
    dest_dir.mkdir(parents=True, exist_ok=True)
    name = f"{uuid.uuid4().hex}{suffix}"
    (dest_dir / name).write_bytes(data)
    return name


def extract_epub(data: bytes) -> dict:
    try:
        import ebooklib
        from ebooklib import epub

        book = epub.read_epub(io.BytesIO(data))
        meta: dict = {
            "title": "",
            "author": "",
            "description": "",
            "language": "",
            "cover_data": None,
        }
        title = book.get_metadata("DC", "title")
        if title:
            meta["title"] = title[0][0]
        creator = book.get_metadata("DC", "creator")
        if creator:
            meta["author"] = creator[0][0]
        desc = book.get_metadata("DC", "description")
        if desc:
            meta["description"] = desc[0][0]
        lang = book.get_metadata("DC", "language")
        if lang:
            meta["language"] = lang[0][0]

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


def extract_fb2(data: bytes) -> dict:
    try:
        from lxml import etree

        ns = {
            "fb": "http://www.gribuser.ru/xml/fictionbook/2.0",
            "l": "http://www.w3.org/1999/xlink",
        }
        root = etree.fromstring(data)
        meta: dict = {"title": "", "author": "", "description": "", "language": "", "cover_data": None}

        title_el = root.find(".//fb:book-title", ns)
        if title_el is not None and title_el.text:
            meta["title"] = title_el.text.strip()

        fn = root.find(".//fb:first-name", ns)
        ln = root.find(".//fb:last-name", ns)
        parts = []
        if fn is not None and fn.text:
            parts.append(fn.text.strip())
        if ln is not None and ln.text:
            parts.append(ln.text.strip())
        meta["author"] = " ".join(parts)

        ann = root.find(".//fb:annotation", ns)
        if ann is not None:
            meta["description"] = etree.tostring(ann, method="text", encoding="unicode").strip()

        lang = root.find(".//fb:lang", ns)
        if lang is not None and lang.text:
            meta["language"] = lang.text.strip()

        cover_page = root.find(".//fb:coverpage", ns)
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
        return {
            "title": info.get("/Title", ""),
            "author": info.get("/Author", ""),
            "description": info.get("/Subject", ""),
            "language": "",
            "cover_data": None,
        }
    except Exception:
        return {}


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

        if isinstance(html_bytes, bytes):
            doc = lhtml.fromstring(html_bytes)
        else:
            doc = lhtml.fromstring(html_bytes.encode("utf-8"))
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

        book = epub.read_epub(io.BytesIO(data))
        docs = {it.get_id(): it for it in book.get_items_of_type(ebooklib.ITEM_DOCUMENT)}
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
