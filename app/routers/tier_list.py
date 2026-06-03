"""Тир-лист циклов: пользователь раскладывает свои циклы по рангам S/A/B/C/D."""
from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.dependencies import get_db, get_current_user
from app.models.user import User
from app.models.series import Series
from app.models.book import Book
from app.models.series_tier import SeriesTier

router = APIRouter(prefix="/tier-list")
templates = Jinja2Templates(directory="app/templates")

TIERS = ["S", "A", "B", "C", "D"]
TIER_COLORS = {"S": "#ff7f7f", "A": "#ffbf7f", "B": "#ffdf7f", "C": "#ffff7f", "D": "#bfff7f"}


def _user_series(db: Session, user: User):
    return (
        db.query(Series)
        .join(Book, Book.series_id == Series.id)
        .filter(Book.user_id == user.id)
        .distinct()
        .order_by(Series.name)
        .all()
    )


def _series_cover(db: Session, user: User, series_id: int):
    b = (
        db.query(Book)
        .filter(Book.user_id == user.id, Book.series_id == series_id, Book.cover_path.isnot(None))
        .order_by(Book.series_order)
        .first()
    )
    return f"/books/{b.id}/cover" if b else None


@router.get("", response_class=HTMLResponse)
@router.get("/", response_class=HTMLResponse)
def tier_list(request: Request, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    series = _user_series(db, user)
    tier_map = {t.series_id: t.tier for t in db.query(SeriesTier).filter(SeriesTier.user_id == user.id).all()}

    def card(s):
        return {"id": s.id, "name": s.name, "author": s.author.name if s.author else "",
                "cover": _series_cover(db, user, s.id)}

    rows = {t: [] for t in TIERS}
    unranked = []
    for s in series:
        t = tier_map.get(s.id)
        (rows[t] if t in TIERS else unranked).append(card(s))

    return templates.TemplateResponse("tier_list.html", {
        "request": request, "user": user, "tiers": TIERS, "tier_colors": TIER_COLORS,
        "rows": rows, "unranked": unranked, "total": len(series),
    })


@router.post("/set")
def set_tier(
    series_id: int = Form(...),
    tier: str = Form(""),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    # цикл должен присутствовать в библиотеке пользователя
    owns = db.query(Book).filter(Book.user_id == user.id, Book.series_id == series_id).first()
    if not owns:
        return JSONResponse({"ok": False}, status_code=404)
    row = db.query(SeriesTier).filter_by(user_id=user.id, series_id=series_id).first()
    if tier in TIERS:
        if row:
            row.tier = tier
        else:
            db.add(SeriesTier(user_id=user.id, series_id=series_id, tier=tier))
    elif row:
        db.delete(row)
    db.commit()
    return JSONResponse({"ok": True})
