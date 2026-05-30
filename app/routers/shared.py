"""Items shared with / by the current user."""
from datetime import datetime
from fastapi import APIRouter, Depends, Request, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.dependencies import get_db, get_current_user
from app.models.user import User
from app.models.share import Share
from app.models.book import Book
from app.models.link import Link, LinkFolder
from app.models.shelf import Shelf

router = APIRouter(prefix="/shared")
templates = Jinja2Templates(directory="app/templates")


def _group_shares(shares, db: Session) -> dict:
    """Group a list of Share rows by resource type and resolve the resources."""
    book_shares = [s for s in shares if s.resource_type == "book"]
    link_shares = [s for s in shares if s.resource_type == "link"]
    shelf_shares = [s for s in shares if s.resource_type == "shelf"]
    folder_shares = [s for s in shares if s.resource_type == "link_folder"]

    books: dict = {}
    if book_shares:
        ids = [s.resource_id for s in book_shares]
        books = {b.id: b for b in db.query(Book).filter(Book.id.in_(ids)).all()}

    links: dict = {}
    if link_shares:
        ids = [s.resource_id for s in link_shares]
        links = {l.id: l for l in db.query(Link).filter(Link.id.in_(ids)).all()}

    shelves: dict = {}
    if shelf_shares:
        ids = [s.resource_id for s in shelf_shares]
        shelves = {s.id: s for s in db.query(Shelf).filter(Shelf.id.in_(ids)).all()}

    folders: dict = {}
    if folder_shares:
        ids = [s.resource_id for s in folder_shares]
        folders = {f.id: f for f in db.query(LinkFolder).filter(LinkFolder.id.in_(ids)).all()}

    return {
        "book_shares": book_shares,
        "link_shares": link_shares,
        "shelf_shares": shelf_shares,
        "folder_shares": folder_shares,
        "books": books,
        "links": links,
        "shelves": shelves,
        "folders": folders,
        "empty": not (book_shares or link_shares or shelf_shares or folder_shares),
    }


@router.get("", response_class=HTMLResponse)
def shared_index(
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Combined page: incoming (shared with me) + outgoing (shared by me) shares."""
    now = datetime.utcnow()
    incoming = (
        db.query(Share)
        .filter(
            Share.shared_with_user_id == user.id,
            Share.is_public == False,
            Share.expires_at > now,
        )
        .order_by(Share.created_at.desc())
        .all()
    )
    outgoing = (
        db.query(Share)
        .filter(
            Share.owner_id == user.id,
            Share.is_public == False,
            Share.expires_at > now,
        )
        .order_by(Share.created_at.desc())
        .all()
    )
    return templates.TemplateResponse("shared/index.html", {
        "request": request,
        "user": user,
        "inc": _group_shares(incoming, db),
        "out": _group_shares(outgoing, db),
    })


@router.get("/shelf/{share_id}", response_class=HTMLResponse)
def view_shared_shelf(
    share_id: int,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    share = db.query(Share).filter(
        Share.id == share_id,
        Share.resource_type == "shelf",
        Share.shared_with_user_id == user.id,
        Share.is_public == False,
        Share.expires_at > datetime.utcnow(),
    ).first()
    if not share:
        raise HTTPException(status_code=404, detail="Доступ не найден или истёк")

    shelf = db.query(Shelf).filter(Shelf.id == share.resource_id).first()
    if not shelf:
        raise HTTPException(status_code=404, detail="Полка не найдена")

    from app.models.author import Author
    authors_with_books = (
        db.query(Author)
        .join(Book, Book.author_id == Author.id)
        .filter(Book.shelf_id == shelf.id)
        .distinct()
        .order_by(Author.name)
        .all()
    )
    return templates.TemplateResponse("shared/shelf.html", {
        "request": request, "user": user,
        "shelf": shelf, "share": share, "authors": authors_with_books,
    })


@router.get("/folder/{share_id}", response_class=HTMLResponse)
def view_shared_folder(
    share_id: int,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    share = db.query(Share).filter(
        Share.id == share_id,
        Share.resource_type == "link_folder",
        Share.shared_with_user_id == user.id,
        Share.is_public == False,
        Share.expires_at > datetime.utcnow(),
    ).first()
    if not share:
        raise HTTPException(status_code=404, detail="Доступ не найден или истёк")

    folder = db.query(LinkFolder).filter(LinkFolder.id == share.resource_id).first()
    if not folder:
        raise HTTPException(status_code=404, detail="Папка не найдена")

    links = db.query(Link).filter(Link.folder_id == folder.id).order_by(Link.created_at.desc()).all()
    return templates.TemplateResponse("shared/folder.html", {
        "request": request, "user": user,
        "folder": folder, "share": share, "links": links,
    })


@router.get("/out")
def my_outgoing_shares():
    """Kept for backward compatibility — outgoing shares are now a tab on /shared."""
    return RedirectResponse("/shared#out", status_code=302)
