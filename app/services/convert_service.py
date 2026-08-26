"""Конвертация книг между форматами: fb2<->epub, epub/fb2->pdf.

PDF как исходный формат намеренно не поддерживается — это неструктурированный
источник, надёжно восстановить из него epub/fb2 нельзя без OCR/тяжёлой
эвристики, поэтому для этого направления показываем понятное "не поддерживается"
вместо хрупкого best-effort.
"""
import io
import uuid
from html import escape

from app.services.book_service import convert_fb2_to_html, extract_epub_text

SUPPORTED_TARGETS = {
    "fb2": ["epub", "pdf"],
    "epub": ["fb2", "pdf"],
}


def targets_for(source_format: str) -> list:
    return SUPPORTED_TARGETS.get((source_format or "").lower(), [])


def _wrap_html(title: str, body_html: str) -> str:
    return (
        "<!doctype html><html><head><meta charset='utf-8'>"
        f"<title>{escape(title)}</title>"
        "<style>"
        "body{font-family:Georgia,'Times New Roman',serif;max-width:700px;margin:2em auto;"
        "line-height:1.6;padding:0 1em}"
        "h1{font-size:1.6em} h3,h4{margin-top:1.5em} img{max-width:100%}"
        "</style></head><body>"
        f"<h1>{escape(title)}</h1>{body_html}</body></html>"
    )


def _paragraphs_html(paragraphs: list) -> str:
    return "".join(f"<p>{escape(p)}</p>" for p in paragraphs)


def convert_fb2_to_epub_bytes(data: bytes, title: str, author: str) -> bytes:
    from ebooklib import epub

    html_body = convert_fb2_to_html(data)
    book = epub.EpubBook()
    book.set_identifier(uuid.uuid4().hex)
    book.set_title(title or "Без названия")
    book.set_language("ru")
    if author:
        book.add_author(author)

    chapter = epub.EpubHtml(title=title or "Текст", file_name="chap_01.xhtml", lang="ru")
    chapter.content = f"<html><body>{html_body}</body></html>"
    book.add_item(chapter)
    book.toc = (chapter,)
    book.add_item(epub.EpubNcx())
    book.add_item(epub.EpubNav())
    book.spine = ["nav", chapter]

    buf = io.BytesIO()
    epub.write_epub(buf, book)
    return buf.getvalue()


def convert_epub_to_fb2_bytes(data: bytes, title: str, author: str) -> bytes:
    paragraphs = extract_epub_text(data)
    first, last = author, ""
    if author and " " in author:
        first, last = author.split(" ", 1)
    body = _paragraphs_html(paragraphs)
    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<FictionBook xmlns="http://www.gribuser.ru/xml/fictionbook/2.0" '
        'xmlns:l="http://www.w3.org/1999/xlink">'
        "<description><title-info>"
        f"<book-title>{escape(title or 'Без названия')}</book-title>"
        f"<author><first-name>{escape(first or '')}</first-name>"
        f"<last-name>{escape(last)}</last-name></author>"
        "</title-info></description>"
        f"<body><section>{body}</section></body>"
        "</FictionBook>"
    )
    return xml.encode("utf-8")


def convert_to_pdf_bytes(source_format: str, data: bytes, title: str) -> bytes:
    from weasyprint import HTML

    if source_format == "fb2":
        body_html = convert_fb2_to_html(data)
    else:
        body_html = _paragraphs_html(extract_epub_text(data))
    return HTML(string=_wrap_html(title, body_html)).write_pdf()


def convert(source_format: str, target_format: str, data: bytes, title: str, author: str) -> bytes:
    source_format = (source_format or "").lower()
    target_format = (target_format or "").lower()
    if target_format not in targets_for(source_format):
        raise ValueError(f"Конвертация {source_format} → {target_format} не поддерживается")
    if target_format == "pdf":
        return convert_to_pdf_bytes(source_format, data, title)
    if source_format == "fb2" and target_format == "epub":
        return convert_fb2_to_epub_bytes(data, title, author)
    if source_format == "epub" and target_format == "fb2":
        return convert_epub_to_fb2_bytes(data, title, author)
    raise ValueError("Неподдерживаемая конвертация")
