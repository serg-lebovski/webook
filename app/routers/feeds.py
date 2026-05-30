from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.dependencies import get_db, get_current_user
from app.models.user import User
from app.models.feed import Feed
from app.models.link import LinkFolder
from app.services.feed_service import refresh_feed

router = APIRouter(prefix="/feeds")
templates = Jinja2Templates(directory="app/templates")


@router.get("", response_class=HTMLResponse)
def feeds_page(
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    feeds = db.query(Feed).filter_by(user_id=user.id).order_by(Feed.created_at.desc()).all()
    folders = db.query(LinkFolder).filter_by(user_id=user.id).order_by(LinkFolder.name).all()
    return templates.TemplateResponse("feeds/index.html", {
        "request": request, "user": user, "feeds": feeds, "folders": folders,
    })


@router.post("")
def add_feed(
    url: str = Form(...),
    folder_id: int = Form(0),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    url = url.strip()
    if url and not url.startswith(("http://", "https://")):
        url = "https://" + url
    if url and not db.query(Feed).filter_by(user_id=user.id, url=url).first():
        feed = Feed(user_id=user.id, url=url, folder_id=folder_id or None)
        db.add(feed)
        db.commit()
        db.refresh(feed)
        try:
            refresh_feed(feed, db)
        except Exception:
            pass
    return RedirectResponse("/feeds", status_code=302)


@router.post("/{feed_id}/refresh")
def refresh_one(
    feed_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    feed = db.query(Feed).filter_by(id=feed_id, user_id=user.id).first()
    if feed:
        try:
            refresh_feed(feed, db)
        except Exception:
            pass
    return RedirectResponse("/feeds", status_code=302)


@router.post("/{feed_id}/delete")
def delete_feed(
    feed_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    feed = db.query(Feed).filter_by(id=feed_id, user_id=user.id).first()
    if feed:
        db.delete(feed)
        db.commit()
    return RedirectResponse("/feeds", status_code=302)
