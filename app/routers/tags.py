from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.dependencies import get_db, get_current_user
from app.models.user import User
from app.services.tag_service import tag_usage, rename_tag, merge_tags, delete_tag

router = APIRouter(prefix="/tags")
templates = Jinja2Templates(directory="app/templates")


@router.get("", response_class=HTMLResponse)
def tags_page(
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    usage = tag_usage(db, user.id)
    return templates.TemplateResponse("tags/index.html", {
        "request": request, "user": user, "usage": usage,
    })


@router.post("/{tag_id}/rename")
def rename(
    tag_id: int,
    name: str = Form(...),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    rename_tag(db, tag_id, name, user.id)
    return RedirectResponse("/tags", status_code=302)


@router.post("/merge")
def merge(
    source_id: int = Form(...),
    target_id: int = Form(...),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    merge_tags(db, source_id, target_id, user.id)
    return RedirectResponse("/tags", status_code=302)


@router.post("/{tag_id}/delete")
def delete(
    tag_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    delete_tag(db, tag_id, user.id)
    return RedirectResponse("/tags", status_code=302)
