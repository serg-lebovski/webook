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
from app.models.stored_file import StoredFile, FileFolder
from app.config import FILES_DIR

router = APIRouter(prefix="/shared")
templates = Jinja2Templates(directory="app/templates")


def _group_shares(shares, db: Session) -> dict:
    """Group a list of Share rows by resource type and resolve the resources."""
    book_shares = [s for s in shares if s.resource_type == "book"]
    link_shares = [s for s in shares if s.resource_type == "link"]
    shelf_shares = [s for s in shares if s.resource_type == "shelf"]
    folder_shares = [s for s in shares if s.resource_type == "link_folder"]
    file_shares = [s for s in shares if s.resource_type == "file"]
    file_folder_shares = [s for s in shares if s.resource_type == "file_folder"]

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

    files: dict = {}
    if file_shares:
        ids = [s.resource_id for s in file_shares]
        files = {f.id: f for f in db.query(StoredFile).filter(StoredFile.id.in_(ids)).all()}

    file_folders: dict = {}
    if file_folder_shares:
        ids = [s.resource_id for s in file_folder_shares]
        file_folders = {f.id: f for f in db.query(FileFolder).filter(FileFolder.id.in_(ids)).all()}

    return {
        "book_shares": book_shares,
        "link_shares": link_shares,
        "shelf_shares": shelf_shares,
        "folder_shares": folder_shares,
        "file_shares": file_shares,
        "file_folder_shares": file_folder_shares,
        "books": books,
        "links": links,
        "shelves": shelves,
        "folders": folders,
        "files": files,
        "file_folders": file_folders,
        "empty": not (book_shares or link_shares or shelf_shares or folder_shares
                      or file_shares or file_folder_shares),
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


def _incoming_share(share_id: int, rtype: str, user: User, db: Session) -> Share:
    share = db.query(Share).filter(
        Share.id == share_id,
        Share.resource_type == rtype,
        Share.shared_with_user_id == user.id,
        Share.is_public == False,
        Share.expires_at > datetime.utcnow(),
    ).first()
    if not share:
        raise HTTPException(status_code=404, detail="Доступ не найден или истёк")
    return share


def _serve_stored(f: StoredFile, *, attachment: bool):
    from fastapi.responses import FileResponse
    path = FILES_DIR / f.stored_name
    if not path.exists():
        raise HTTPException(status_code=404)
    headers = None
    if attachment:
        from urllib.parse import quote
        headers = {"Content-Disposition": f"attachment; filename*=UTF-8''{quote(f.original_name)}"}
    return FileResponse(path, media_type=f.content_type or "application/octet-stream", headers=headers)


@router.get("/file/{share_id}/view")
def shared_file_view(share_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    share = _incoming_share(share_id, "file", user, db)
    f = db.query(StoredFile).filter_by(id=share.resource_id).first()
    if not f:
        raise HTTPException(status_code=404)
    return _serve_stored(f, attachment=False)


@router.get("/file/{share_id}/download")
def shared_file_download(share_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    share = _incoming_share(share_id, "file", user, db)
    f = db.query(StoredFile).filter_by(id=share.resource_id).first()
    if not f:
        raise HTTPException(status_code=404)
    return _serve_stored(f, attachment=True)


@router.get("/files-folder/{share_id}", response_class=HTMLResponse)
def view_shared_file_folder(share_id: int, request: Request,
                            user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    share = _incoming_share(share_id, "file_folder", user, db)
    folder = db.query(FileFolder).filter_by(id=share.resource_id).first()
    if not folder:
        raise HTTPException(status_code=404, detail="Папка не найдена")
    files = (
        db.query(StoredFile)
        .filter_by(folder_id=folder.id, user_id=share.owner_id)
        .order_by(StoredFile.created_at.desc())
        .all()
    )
    return templates.TemplateResponse("shared/files_folder.html", {
        "request": request, "user": user, "folder": folder, "share": share, "files": files,
    })


def _file_in_folder(share_id: int, file_id: int, user: User, db: Session) -> StoredFile:
    share = _incoming_share(share_id, "file_folder", user, db)
    f = db.query(StoredFile).filter_by(id=file_id, folder_id=share.resource_id,
                                       user_id=share.owner_id).first()
    if not f:
        raise HTTPException(status_code=404)
    return f


@router.get("/files-folder/{share_id}/{file_id}/view")
def shared_folder_file_view(share_id: int, file_id: int,
                            user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return _serve_stored(_file_in_folder(share_id, file_id, user, db), attachment=False)


@router.get("/files-folder/{share_id}/{file_id}/download")
def shared_folder_file_download(share_id: int, file_id: int,
                                user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return _serve_stored(_file_in_folder(share_id, file_id, user, db), attachment=True)


@router.get("/out")
def my_outgoing_shares():
    """Kept for backward compatibility — outgoing shares are now a tab on /shared."""
    return RedirectResponse("/shared#out", status_code=302)
