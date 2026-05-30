"""OPDS 1.2 catalog with HTTP Basic Auth."""
from datetime import datetime
from xml.etree.ElementTree import Element, SubElement, tostring
from xml.etree import ElementTree as ET

from fastapi import APIRouter, Depends, Request
from fastapi.responses import Response
from sqlalchemy.orm import Session

from app.dependencies import get_db, opds_auth
from app.models.author import Author
from app.models.book import Book
from app.models.series import Series
from app.models.shelf import Shelf
from app.models.user import User

router = APIRouter(prefix="/opds")

ATOM_NS = "http://www.w3.org/2005/Atom"
OPDS_NS = "http://opds-spec.org/2010/catalog"
DC_NS = "http://purl.org/dc/terms/"

ET.register_namespace("", ATOM_NS)
ET.register_namespace("opds", OPDS_NS)
ET.register_namespace("dc", DC_NS)

MIME_MAP = {
    "epub": "application/epub+zip",
    "fb2": "application/x-fictionbook+xml",
    "pdf": "application/pdf",
}


def _atom(tag, ns=ATOM_NS):
    return f"{{{ns}}}{tag}"


def _feed(title: str, feed_id: str, request: Request) -> Element:
    feed = Element(_atom("feed"))
    _t(feed, "title", title)
    _t(feed, "id", f"urn:webook:{feed_id}")
    _t(feed, "updated", datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"))
    SubElement(feed, _atom("author")).append(_text_el("name", "WeBook"))
    _link(feed, rel="self", href=str(request.url), type="application/atom+xml;profile=opds-catalog;kind=navigation")
    _link(feed, rel="start", href="/opds/", type="application/atom+xml;profile=opds-catalog;kind=navigation")
    _link(feed, rel="search", href="/opds/search?q={searchTerms}", type="application/opensearchdescription+xml")
    return feed


def _t(parent, tag, text):
    el = SubElement(parent, _atom(tag))
    el.text = str(text) if text else ""
    return el


def _text_el(tag, text):
    el = Element(_atom(tag))
    el.text = str(text)
    return el


def _link(parent, rel, href, type_="application/atom+xml;profile=opds-catalog;kind=navigation", **kw):
    attrs = {"rel": rel, "href": href, "type": type_}
    attrs.update(kw)
    SubElement(parent, _atom("link"), attrib=attrs)


def _nav_entry(feed, title, feed_id, href, content=""):
    entry = SubElement(feed, _atom("entry"))
    _t(entry, "title", title)
    _t(entry, "id", f"urn:webook:{feed_id}")
    _t(entry, "updated", datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"))
    if content:
        c = SubElement(entry, _atom("content"))
        c.set("type", "text")
        c.text = content
    _link(entry, rel="subsection", href=href)
    return entry


def _book_entry(feed, book: Book, base_url: str):
    entry = SubElement(feed, _atom("entry"))
    _t(entry, "title", book.title)
    _t(entry, "id", f"urn:webook:book:{book.id}")
    _t(entry, "updated", book.added_at.strftime("%Y-%m-%dT%H:%M:%SZ") if book.added_at else "2000-01-01T00:00:00Z")

    auth_el = SubElement(entry, _atom("author"))
    _t(auth_el, "name", book.author.name if book.author else "")

    if book.description:
        s = SubElement(entry, _atom("summary"))
        s.set("type", "text")
        s.text = book.description

    if book.cover_path:
        _link(entry, rel="http://opds-spec.org/image", href=f"/books/{book.id}/cover", type_="image/jpeg")
        _link(entry, rel="http://opds-spec.org/image/thumbnail", href=f"/books/{book.id}/cover", type_="image/jpeg")

    mime = MIME_MAP.get(book.file_format, "application/octet-stream")
    _link(entry, rel="http://opds-spec.org/acquisition", href=f"/books/{book.id}/download",
          type_=mime)


def _xml_response(feed: Element) -> Response:
    data = b'<?xml version="1.0" encoding="utf-8"?>\n' + tostring(feed, encoding="unicode").encode("utf-8")
    return Response(content=data, media_type="application/atom+xml;charset=utf-8")


@router.get("/")
@router.get("")
def opds_root(request: Request, user: User = Depends(opds_auth), db: Session = Depends(get_db)):
    feed = _feed("WeBook — Библиотека", "root", request)
    _nav_entry(feed, "По полкам", "nav:shelves", "/opds/shelves")
    _nav_entry(feed, "По авторам", "nav:authors", "/opds/authors")
    _nav_entry(feed, "Все книги", "nav:books", "/opds/books")
    return _xml_response(feed)


@router.get("/shelves")
def opds_shelves(request: Request, user: User = Depends(opds_auth), db: Session = Depends(get_db)):
    feed = _feed("Полки", "shelves", request)
    shelves = db.query(Shelf).filter(Shelf.user_id == user.id).order_by(Shelf.name).all()
    for shelf in shelves:
        count = db.query(Book).filter(Book.shelf_id == shelf.id, Book.user_id == user.id).count()
        _nav_entry(feed, shelf.name, f"shelf:{shelf.id}", f"/opds/shelves/{shelf.id}", f"{count} книг")
    return _xml_response(feed)


@router.get("/shelves/{shelf_id}")
def opds_shelf_books(shelf_id: int, request: Request, user: User = Depends(opds_auth), db: Session = Depends(get_db)):
    shelf = db.query(Shelf).filter(Shelf.id == shelf_id, Shelf.user_id == user.id).first()
    if not shelf:
        return _xml_response(_feed("Не найдено", "empty", request))
    feed = _feed(f"Полка: {shelf.name}", f"shelf:{shelf_id}", request)
    books = db.query(Book).filter(Book.shelf_id == shelf_id, Book.user_id == user.id).order_by(Book.title).all()
    for book in books:
        _book_entry(feed, book, str(request.base_url))
    return _xml_response(feed)


@router.get("/authors")
def opds_authors(request: Request, user: User = Depends(opds_auth), db: Session = Depends(get_db)):
    feed = _feed("Авторы", "authors", request)
    authors = (
        db.query(Author)
        .join(Book, Book.author_id == Author.id)
        .filter(Book.user_id == user.id)
        .distinct()
        .order_by(Author.name)
        .all()
    )
    for author in authors:
        count = db.query(Book).filter(Book.author_id == author.id, Book.user_id == user.id).count()
        _nav_entry(feed, author.name, f"author:{author.id}", f"/opds/authors/{author.id}", f"{count} книг")
    return _xml_response(feed)


@router.get("/authors/{author_id}")
def opds_author_books(author_id: int, request: Request, user: User = Depends(opds_auth), db: Session = Depends(get_db)):
    author = db.query(Author).filter(Author.id == author_id).first()
    if not author:
        return _xml_response(_feed("Не найдено", "empty", request))
    feed = _feed(author.name, f"author:{author_id}", request)
    for series in author.series:
        _nav_entry(feed, f"Цикл: {series.name}", f"series:{series.id}", f"/opds/series/{series.id}",
                   f"{len(series.books)} книг")
    standalone = (
        db.query(Book)
        .filter(Book.author_id == author_id, Book.user_id == user.id, Book.series_id == None)  # noqa: E711
        .all()
    )
    for book in standalone:
        _book_entry(feed, book, str(request.base_url))
    return _xml_response(feed)


@router.get("/series/{series_id}")
def opds_series_books(series_id: int, request: Request, user: User = Depends(opds_auth), db: Session = Depends(get_db)):
    series = db.query(Series).filter(Series.id == series_id).first()
    if not series:
        return _xml_response(_feed("Не найдено", "empty", request))
    feed = _feed(f"Цикл: {series.name}", f"series:{series_id}", request)
    books = db.query(Book).filter(Book.series_id == series_id, Book.user_id == user.id).order_by(Book.series_order).all()
    for book in books:
        _book_entry(feed, book, str(request.base_url))
    return _xml_response(feed)


@router.get("/books")
def opds_all_books(request: Request, user: User = Depends(opds_auth), db: Session = Depends(get_db)):
    feed = _feed("Все книги", "all-books", request)
    books = db.query(Book).filter(Book.user_id == user.id).order_by(Book.title).all()
    for book in books:
        _book_entry(feed, book, str(request.base_url))
    return _xml_response(feed)


@router.get("/search")
def opds_search(q: str = "", request: Request = None, user: User = Depends(opds_auth), db: Session = Depends(get_db)):
    feed = _feed(f"Поиск: {q}", "search", request)
    if q:
        books = (
            db.query(Book)
            .filter(Book.user_id == user.id, Book.title.ilike(f"%{q}%"))
            .order_by(Book.title)
            .limit(50)
            .all()
        )
        for book in books:
            _book_entry(feed, book, str(request.base_url))
    return _xml_response(feed)
