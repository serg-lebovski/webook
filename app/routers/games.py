"""Игры: добавление вручную или из Steam, оценки, статусы, комментарии, тир-лист."""
from datetime import datetime
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, Form, Request, UploadFile, File, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse, Response
from sqlalchemy import or_
from sqlalchemy.orm import Session

from fastapi.templating import Jinja2Templates
from app.dependencies import get_db, get_current_user
from app.models.user import User
from app.models.game import Game, GameTier, PLATFORMS, GAME_STATUSES, STATUS_LABELS
from app.services import steam_service, hltb_service, settings_service
from app.services.book_service import save_cover_file, delete_file
from app.config import COVERS_DIR

router = APIRouter(prefix="/games")
templates = Jinja2Templates(directory="app/templates")

TIERS = ["S", "A", "B", "C", "D"]


def _own_game(game_id: int, user: User, db: Session) -> Game:
    g = db.query(Game).filter_by(id=game_id, user_id=user.id, deleted_at=None).first()
    if not g:
        raise HTTPException(status_code=404, detail="Игра не найдена")
    return g


def _apply_form(g: Game, f: dict):
    g.title = (f.get("title") or "").strip()[:200] or g.title
    g.platform = (f.get("platform") or "").strip()[:60]
    g.status = f.get("status") if f.get("status") in STATUS_LABELS else "want"
    g.description = (f.get("description") or "").strip()[:8000]
    g.comment = (f.get("comment") or "").strip()[:4000]
    g.genres = (f.get("genres") or "").strip()[:200]
    g.steam_url = (f.get("steam_url") or "").strip()[:300]
    g.screenshots = (f.get("screenshots") or "").strip()

    def _int(key):
        try:
            v = int(f.get(key))
            return v
        except (TypeError, ValueError):
            return None

    def _float(key):
        try:
            return float(str(f.get(key)).replace(",", "."))
        except (TypeError, ValueError):
            return None

    g.rating = _int("rating") if (f.get("rating") not in (None, "", "0")) else None
    if g.rating is not None:
        g.rating = max(1, min(10, g.rating))
    g.release_year = _int("release_year")
    g.metacritic = _int("metacritic")
    g.steam_appid = _int("steam_appid")
    g.hltb_main = _float("hltb_main")
    g.hltb_completionist = _float("hltb_completionist")
    if g.status == "completed" and g.completed_at is None:
        g.completed_at = datetime.utcnow()
    if g.status != "completed":
        g.completed_at = None


# ─────────────────────────── список ───────────────────────────

@router.get("", response_class=HTMLResponse)
@router.get("/", response_class=HTMLResponse)
def games_list(request: Request, status: str = "", platform: str = "", sort: str = "added",
               user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    q = db.query(Game).filter(Game.user_id == user.id, Game.deleted_at.is_(None))
    if status in STATUS_LABELS:
        q = q.filter(Game.status == status)
    if platform:
        q = q.filter(Game.platform == platform)
    if sort == "rating":
        q = q.order_by(Game.rating.is_(None), Game.rating.desc(), Game.title)
    elif sort == "title":
        q = q.order_by(Game.title)
    else:
        q = q.order_by(Game.created_at.desc())
    games = q.all()
    counts = {key: db.query(Game).filter(Game.user_id == user.id, Game.deleted_at.is_(None),
                                         Game.status == key).count()
              for key, _ in GAME_STATUSES}
    total_hours = round(sum((g.playtime_minutes or 0) for g in
                            db.query(Game).filter_by(user_id=user.id, deleted_at=None).all()) / 60, 1)
    return templates.TemplateResponse("games/list.html", {
        "request": request, "user": user, "games": games, "statuses": GAME_STATUSES,
        "status_labels": STATUS_LABELS, "cur_status": status, "cur_platform": platform,
        "cur_sort": sort, "platforms": PLATFORMS, "counts": counts, "total_hours": total_hours,
    })


# ─────────────────────────── добавление ───────────────────────────

@router.get("/add", response_class=HTMLResponse)
def add_form(request: Request, user: User = Depends(get_current_user)):
    return templates.TemplateResponse("games/form.html", {
        "request": request, "user": user, "game": None,
        "platforms": PLATFORMS, "statuses": GAME_STATUSES,
    })


@router.get("/steam-lookup")
def steam_lookup(url: str = "", user: User = Depends(get_current_user)):
    appid = steam_service.parse_appid(url)
    if not appid:
        return JSONResponse({"error": "Не похоже на ссылку Steam"}, status_code=400)
    data = steam_service.fetch_appdetails(appid)
    if not data:
        return JSONResponse({"error": "Не удалось получить данные из Steam"}, status_code=404)
    return JSONResponse(data)


@router.get("/hltb-lookup")
def hltb_lookup(title: str = "", user: User = Depends(get_current_user)):
    main, comp = hltb_service.search_hours(title)
    return JSONResponse({"main": main, "completionist": comp})


@router.post("")
async def create_game(
    request: Request,
    title: str = Form(""),
    platform: str = Form(""),
    status: str = Form("want"),
    rating: str = Form(""),
    comment: str = Form(""),
    description: str = Form(""),
    genres: str = Form(""),
    release_year: str = Form(""),
    metacritic: str = Form(""),
    steam_appid: str = Form(""),
    steam_url: str = Form(""),
    hltb_main: str = Form(""),
    hltb_completionist: str = Form(""),
    screenshots: str = Form(""),
    cover_url: str = Form(""),
    cover: Optional[UploadFile] = File(None),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if not title.strip():
        return RedirectResponse("/games/add", status_code=302)
    g = Game(user_id=user.id, title=title.strip())
    _apply_form(g, locals())
    db.add(g)
    db.flush()
    # обложка: загруженный файл важнее steam-ссылки
    if cover is not None and cover.filename:
        data = await cover.read()
        if data:
            g.cover_path = save_cover_file(data, Path(cover.filename).suffix.lower() or ".jpg")
    elif cover_url.strip():
        cb = steam_service.fetch_cover_bytes(cover_url.strip())
        if cb:
            g.cover_path = save_cover_file(cb, ".jpg")
    db.commit()
    return RedirectResponse(f"/games/{g.id}", status_code=302)


# ─────────────────────────── тир-лист ───────────────────────────

@router.get("/tier-list", response_class=HTMLResponse)
def tier_list(request: Request, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    games = db.query(Game).filter(Game.user_id == user.id, Game.deleted_at.is_(None)).all()
    tiers = {t.game_id: t.tier for t in db.query(GameTier).filter_by(user_id=user.id).all()}
    buckets = {t: [] for t in TIERS}
    unranked = []
    for g in games:
        t = tiers.get(g.id)
        (buckets[t] if t in buckets else unranked).append(g) if t else unranked.append(g)
    for t in buckets:
        buckets[t].sort(key=lambda g: (g.rating or 0), reverse=True)
    return templates.TemplateResponse("games/tier_list.html", {
        "request": request, "user": user, "tiers": TIERS,
        "buckets": buckets, "unranked": unranked,
    })


@router.post("/tier-list/set")
def tier_set(game_id: int = Form(...), tier: str = Form(...),
             user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    g = db.query(Game).filter_by(id=game_id, user_id=user.id, deleted_at=None).first()
    if not g:
        return JSONResponse({"error": "not found"}, status_code=404)
    row = db.query(GameTier).filter_by(user_id=user.id, game_id=game_id).first()
    if tier == "":
        if row:
            db.delete(row)
    elif tier in TIERS:
        if row:
            row.tier = tier
        else:
            db.add(GameTier(user_id=user.id, game_id=game_id, tier=tier))
    db.commit()
    return JSONResponse({"ok": True})


# ─────────────────────────── деталь / правка ───────────────────────────

@router.get("/{game_id}", response_class=HTMLResponse)
def game_detail(game_id: int, request: Request,
                user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    g = _own_game(game_id, user, db)
    tier = db.query(GameTier).filter_by(user_id=user.id, game_id=g.id).first()
    return templates.TemplateResponse("games/detail.html", {
        "request": request, "user": user, "game": g,
        "status_labels": STATUS_LABELS, "statuses": GAME_STATUSES,
        "tier": tier.tier if tier else "", "tiers": TIERS,
    })


@router.get("/{game_id}/edit", response_class=HTMLResponse)
def edit_form(game_id: int, request: Request,
              user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    g = _own_game(game_id, user, db)
    return templates.TemplateResponse("games/form.html", {
        "request": request, "user": user, "game": g,
        "platforms": PLATFORMS, "statuses": GAME_STATUSES,
    })


@router.post("/{game_id}/edit")
async def edit_game(
    game_id: int,
    request: Request,
    title: str = Form(""), platform: str = Form(""), status: str = Form("want"),
    rating: str = Form(""), comment: str = Form(""), description: str = Form(""),
    genres: str = Form(""), release_year: str = Form(""), metacritic: str = Form(""),
    steam_appid: str = Form(""), steam_url: str = Form(""),
    hltb_main: str = Form(""), hltb_completionist: str = Form(""),
    screenshots: str = Form(""), cover_url: str = Form(""),
    cover: Optional[UploadFile] = File(None),
    user: User = Depends(get_current_user), db: Session = Depends(get_db),
):
    g = _own_game(game_id, user, db)
    _apply_form(g, locals())
    if cover is not None and cover.filename:
        data = await cover.read()
        if data:
            if g.cover_path:
                delete_file(g.cover_path, COVERS_DIR)
            g.cover_path = save_cover_file(data, Path(cover.filename).suffix.lower() or ".jpg")
    elif cover_url.strip() and not g.cover_path:
        cb = steam_service.fetch_cover_bytes(cover_url.strip())
        if cb:
            g.cover_path = save_cover_file(cb, ".jpg")
    db.commit()
    return RedirectResponse(f"/games/{g.id}", status_code=302)


@router.post("/{game_id}/rate")
def rate_game(game_id: int, rating: int = Form(...),
              user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    g = _own_game(game_id, user, db)
    g.rating = None if rating <= 0 else max(1, min(10, rating))
    db.commit()
    return RedirectResponse(f"/games/{g.id}", status_code=302)


@router.post("/{game_id}/status")
def set_status(game_id: int, status: str = Form(...),
               user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    g = _own_game(game_id, user, db)
    if status in STATUS_LABELS:
        g.status = status
        g.completed_at = datetime.utcnow() if status == "completed" else None
        db.commit()
    return RedirectResponse(f"/games/{g.id}", status_code=302)


@router.post("/{game_id}/favorite")
def fav_game(game_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    g = _own_game(game_id, user, db)
    g.is_favorite = not g.is_favorite
    db.commit()
    return RedirectResponse(f"/games/{g.id}", status_code=302)


@router.post("/{game_id}/comment")
def comment_game(game_id: int, comment: str = Form(""),
                 user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    g = _own_game(game_id, user, db)
    g.comment = comment.strip()[:4000]
    db.commit()
    return RedirectResponse(f"/games/{g.id}", status_code=302)


@router.post("/{game_id}/refresh-steam")
def refresh_steam(game_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    g = _own_game(game_id, user, db)
    appid = g.steam_appid or steam_service.parse_appid(g.steam_url)
    if appid:
        data = steam_service.fetch_appdetails(appid)
        if data:
            g.description = data["description"] or g.description
            g.genres = data["genres"] or g.genres
            g.release_year = data["release_year"] or g.release_year
            g.metacritic = data["metacritic"] or g.metacritic
            g.screenshots = "\n".join(data["screenshots"]) or g.screenshots
            g.steam_appid = appid
            g.steam_url = data["steam_url"]
            if not g.cover_path and data["cover_url"]:
                cb = steam_service.fetch_cover_bytes(data["cover_url"])
                if cb:
                    g.cover_path = save_cover_file(cb, ".jpg")
            db.commit()
    return RedirectResponse(f"/games/{g.id}", status_code=302)


@router.post("/{game_id}/sync-playtime")
def sync_playtime(game_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    g = _own_game(game_id, user, db)
    api_key = settings_service.get_setting(db, "steam_api_key", "")
    profile = getattr(user, "steam_profile_url", "") or getattr(user, "steam_id", "")
    appid = g.steam_appid or steam_service.parse_appid(g.steam_url)
    if api_key and profile and appid:
        steamid = steam_service.resolve_steamid(profile, api_key)
        if steamid:
            pt = steam_service.owned_playtimes(api_key, steamid)
            if appid in pt:
                g.playtime_minutes = pt[appid]
                db.commit()
    return RedirectResponse(f"/games/{g.id}", status_code=302)


@router.post("/{game_id}/delete")
def delete_game(game_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    g = _own_game(game_id, user, db)
    g.deleted_at = datetime.utcnow()
    db.commit()
    return RedirectResponse("/games", status_code=302)


@router.get("/{game_id}/cover")
def game_cover(game_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    g = _own_game(game_id, user, db)
    if not g.cover_path:
        raise HTTPException(status_code=404)
    path = COVERS_DIR / g.cover_path
    if not path.is_file():
        raise HTTPException(status_code=404)
    return Response(content=path.read_bytes(), media_type="image/jpeg")
