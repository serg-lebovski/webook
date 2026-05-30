from datetime import datetime

from fastapi import APIRouter, Depends, Form, Request, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse, Response
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.dependencies import get_db, get_current_user
from app.models.user import User
from app.models.highlight import Highlight
from app.models.book import Book
from app.models.link import Link

router = APIRouter(prefix="/highlights")
templates = Jinja2Templates(directory="app/templates")


class HighlightCreate(BaseModel):
    resource_type: str
    resource_id: int
    quote: str
    location: str | None = None
    color: str = "yellow"


def _resource_exists(rtype: str, rid: int, user: User, db: Session) -> bool:
    if rtype == "book":
        return db.query(Book).filter(Book.id == rid).first() is not None
    if rtype == "link":
        return db.query(Link).filter(Link.id == rid, Link.user_id == user.id).first() is not None
    return False


@router.post("")
def create_highlight(
    body: HighlightCreate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if body.resource_type not in ("book", "link"):
        raise HTTPException(status_code=400, detail="Неверный тип ресурса")
    quote = (body.quote or "").strip()
    if not quote:
        raise HTTPException(status_code=400, detail="Пустая цитата")
    if not _resource_exists(body.resource_type, body.resource_id, user, db):
        raise HTTPException(status_code=404, detail="Ресурс не найден")
    h = Highlight(
        user_id=user.id,
        resource_type=body.resource_type,
        resource_id=body.resource_id,
        quote=quote[:5000],
        location=body.location,
        color=body.color or "yellow",
    )
    db.add(h)
    db.commit()
    db.refresh(h)
    return {"id": h.id, "location": h.location}


@router.get("/list")
def list_for_resource(
    resource_type: str,
    resource_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    rows = (
        db.query(Highlight)
        .filter(
            Highlight.user_id == user.id,
            Highlight.resource_type == resource_type,
            Highlight.resource_id == resource_id,
        )
        .order_by(Highlight.id)
        .all()
    )
    return JSONResponse([
        {"id": h.id, "quote": h.quote, "note": h.note or "", "location": h.location, "color": h.color}
        for h in rows
    ])


def _grouped_highlights(db: Session, user_id: int) -> list:
    """Цитаты пользователя, сгруппированные по источнику (книга/статья)."""
    rows = (
        db.query(Highlight)
        .filter(Highlight.user_id == user_id)
        .order_by(Highlight.resource_type, Highlight.resource_id, Highlight.created_at.desc())
        .all()
    )
    groups: dict = {}
    for h in rows:
        key = (h.resource_type, h.resource_id)
        if key not in groups:
            if h.resource_type == "book":
                obj = db.query(Book).filter(Book.id == h.resource_id).first()
                title = obj.title if obj else "Удалённая книга"
                url = f"/books/{h.resource_id}/read"
            else:
                obj = db.query(Link).filter(Link.id == h.resource_id).first()
                title = obj.title if obj else "Удалённая статья"
                url = f"/links/{h.resource_id}/read"
            groups[key] = {"type": h.resource_type, "title": title, "url": url, "items": []}
        groups[key]["items"].append(h)
    return list(groups.values())


@router.get("", response_class=HTMLResponse)
def my_highlights(
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    groups = _grouped_highlights(db, user.id)
    total = sum(len(g["items"]) for g in groups)
    return templates.TemplateResponse("highlights/index.html", {
        "request": request, "user": user, "groups": groups, "total": total,
    })


@router.get("/export")
def export_markdown(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Выгрузка всех цитат пользователя в один Markdown-файл (для Obsidian и т.п.)."""
    groups = _grouped_highlights(db, user.id)
    total = sum(len(g["items"]) for g in groups)
    today = datetime.utcnow().strftime("%d.%m.%Y")

    lines = ["# Мои цитаты — WeBook", "", f"_Выгружено {today} · всего цитат: {total}_", ""]
    for g in groups:
        icon = "📖" if g["type"] == "book" else "🔖"
        lines.append(f"## {icon} {g['title']}")
        lines.append("")
        for h in g["items"]:
            for ln in (h.quote or "").strip().splitlines() or [""]:
                lines.append(f"> {ln}")
            if h.note:
                lines.append(">")
                lines.append(f"> 💬 {h.note}")
            date = h.created_at.strftime("%d.%m.%Y") if h.created_at else ""
            lines.append("")
            if date:
                lines.append(f"<small>— {date}</small>")
            lines.append("")
        lines.append("---")
        lines.append("")

    md = "\n".join(lines)
    fname = f"webook_highlights_{datetime.utcnow().strftime('%Y-%m-%d')}.md"
    return Response(
        content=md.encode("utf-8"),
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{fname}"'},
    )


@router.post("/{hid}/note")
def set_note(
    hid: int,
    note: str = Form(""),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    h = db.query(Highlight).filter_by(id=hid, user_id=user.id).first()
    if h:
        h.note = note.strip()
        db.commit()
    return RedirectResponse("/highlights", status_code=302)


@router.post("/{hid}/delete")
def delete_highlight(
    hid: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    h = db.query(Highlight).filter_by(id=hid, user_id=user.id).first()
    if h:
        db.delete(h)
        db.commit()
    return RedirectResponse("/highlights", status_code=302)
