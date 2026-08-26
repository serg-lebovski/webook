"""Именованные закладки в книге/статье — дополнение к единственной автопозиции чтения."""
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.dependencies import get_db, get_current_user
from app.models.user import User
from app.models.bookmark import Bookmark
from app.models.book import Book
from app.models.link import Link

router = APIRouter(prefix="/bookmarks")


class BookmarkCreate(BaseModel):
    resource_type: str
    resource_id: int
    location: str
    label: str = ""


def _resource_exists(rtype: str, rid: int, user: User, db: Session) -> bool:
    if rtype == "book":
        return db.query(Book).filter(Book.id == rid).first() is not None
    if rtype == "link":
        return db.query(Link).filter(Link.id == rid, Link.user_id == user.id).first() is not None
    return False


@router.post("")
def create_bookmark(
    body: BookmarkCreate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if body.resource_type not in ("book", "link"):
        raise HTTPException(status_code=400, detail="Неверный тип ресурса")
    location = (body.location or "").strip()
    if not location:
        raise HTTPException(status_code=400, detail="Пустая позиция")
    if not _resource_exists(body.resource_type, body.resource_id, user, db):
        raise HTTPException(status_code=404, detail="Ресурс не найден")
    b = Bookmark(
        user_id=user.id,
        resource_type=body.resource_type,
        resource_id=body.resource_id,
        location=location,
        label=(body.label or "").strip()[:200],
    )
    db.add(b)
    db.commit()
    db.refresh(b)
    return {"id": b.id, "location": b.location, "label": b.label}


@router.get("/list")
def list_for_resource(
    resource_type: str,
    resource_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    rows = (
        db.query(Bookmark)
        .filter(
            Bookmark.user_id == user.id,
            Bookmark.resource_type == resource_type,
            Bookmark.resource_id == resource_id,
        )
        .order_by(Bookmark.created_at)
        .all()
    )
    return JSONResponse([
        {"id": b.id, "location": b.location, "label": b.label,
         "created_at": b.created_at.strftime("%d.%m.%Y %H:%M") if b.created_at else ""}
        for b in rows
    ])


@router.post("/{bid}/delete")
def delete_bookmark(bid: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    b = db.query(Bookmark).filter_by(id=bid, user_id=user.id).first()
    if b:
        db.delete(b)
        db.commit()
    return JSONResponse({"ok": True})
