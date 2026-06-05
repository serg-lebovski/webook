"""Корзина: просмотр удалённого, восстановление и окончательное удаление."""
from fastapi import APIRouter, Depends, Form, Request, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.dependencies import get_db, get_current_user
from app.models.user import User
from app.models.book import Book
from app.models.link import Link
from app.models.stored_file import StoredFile
from app.services import trash_service

router = APIRouter(prefix="/trash")
templates = Jinja2Templates(directory="app/templates")


@router.get("", response_class=HTMLResponse)
def trash_index(request: Request, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    # ленивая авто-очистка старше RETENTION_DAYS
    trash_service.purge_expired(db, user_id=user.id)

    books = (db.query(Book)
             .filter(Book.user_id == user.id, Book.deleted_at.isnot(None))
             .order_by(Book.deleted_at.desc()).all())
    links = (db.query(Link)
             .filter(Link.user_id == user.id, Link.deleted_at.isnot(None))
             .order_by(Link.deleted_at.desc()).all())
    files = (db.query(StoredFile)
             .filter(StoredFile.user_id == user.id, StoredFile.deleted_at.isnot(None))
             .order_by(StoredFile.deleted_at.desc()).all())
    return templates.TemplateResponse("trash.html", {
        "request": request, "user": user,
        "books": books, "links": links, "files": files,
        "retention": trash_service.RETENTION_DAYS,
        "empty": not (books or links or files),
    })


def _get(model, item_id: int, user: User, db: Session):
    row = db.query(model).filter(
        model.id == item_id, model.user_id == user.id, model.deleted_at.isnot(None)
    ).first()
    if not row:
        raise HTTPException(status_code=404, detail="Элемент не найден в корзине")
    return row


_MODELS = {"book": Book, "link": Link, "file": StoredFile}
_RESTORE = {"book": trash_service.restore_book, "link": trash_service.restore_link, "file": trash_service.restore_file}
_PURGE = {"book": trash_service.purge_book, "link": trash_service.purge_link, "file": trash_service.purge_file}


@router.post("/restore")
def restore(kind: str = Form(...), item_id: int = Form(...),
            user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    model = _MODELS.get(kind)
    if not model:
        raise HTTPException(status_code=400)
    row = _get(model, item_id, user, db)
    _RESTORE[kind](row, db)
    db.commit()
    return RedirectResponse("/trash", status_code=302)


@router.post("/purge")
def purge(kind: str = Form(...), item_id: int = Form(...),
          user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    model = _MODELS.get(kind)
    if not model:
        raise HTTPException(status_code=400)
    row = _get(model, item_id, user, db)
    _PURGE[kind](row, db)
    db.commit()
    return RedirectResponse("/trash", status_code=302)


@router.post("/empty")
def empty(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    for kind, model in _MODELS.items():
        rows = db.query(model).filter(
            model.user_id == user.id, model.deleted_at.isnot(None)
        ).all()
        for row in rows:
            _PURGE[kind](row, db)
    db.commit()
    return RedirectResponse("/trash", status_code=302)
