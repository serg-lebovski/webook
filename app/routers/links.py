from datetime import datetime
from typing import List
from urllib.parse import urlencode
from fastapi import APIRouter, Depends, Form, Request, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.dependencies import get_db, get_current_user
from app.models.user import User
from app.models.link import Link, LinkFolder
from app.models.tag import Tag
from app.services.fetch_service import fetch_link_data, fetch_meta_only
from app.services.tag_service import set_tags_from_string, tags_to_string, get_or_create_tags, parse_tag_names

router = APIRouter(prefix="/links")
templates = Jinja2Templates(directory="app/templates")

LINKS_PER_PAGE = 50


# ---------- Content file helpers ----------

def _save_content(link_id: int, content):
    if not content:
        return
    from app.config import LINKS_CONTENT_DIR
    LINKS_CONTENT_DIR.mkdir(parents=True, exist_ok=True)
    (LINKS_CONTENT_DIR / f"{link_id}.txt").write_text(content, encoding="utf-8")


# ---------- Helpers ----------

def _get_folder_or_404(folder_id: int, user_id: int, db: Session) -> LinkFolder:
    folder = db.query(LinkFolder).filter_by(id=folder_id, user_id=user_id).first()
    if not folder:
        raise HTTPException(status_code=404, detail="Папка не найдена")
    return folder


def _get_link_or_404(link_id: int, user_id: int, db: Session) -> Link:
    link = db.query(Link).filter_by(id=link_id, user_id=user_id, deleted_at=None).first()
    if not link:
        raise HTTPException(status_code=404, detail="Ссылка не найдена")
    return link


def _get_public_link_share(link_id: int, db: Session):
    from app.models.share import Share
    return db.query(Share).filter(
        Share.resource_type == "link",
        Share.resource_id == link_id,
        Share.is_public == True,
        Share.expires_at > datetime.utcnow(),
    ).first()


# ---------- Links list ----------

@router.get("", response_class=HTMLResponse)
def links_list(
    request: Request,
    folder_id: int = 0,
    show: str = "all",
    tag: str = "",
    page: int = 1,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    folders = db.query(LinkFolder).filter_by(user_id=user.id).order_by(LinkFolder.sort_order, LinkFolder.name).all()

    q = db.query(Link).filter_by(user_id=user.id, deleted_at=None)
    if folder_id:
        q = q.filter_by(folder_id=folder_id)
    if show == "unread":
        q = q.filter_by(is_read=False)
    elif show == "read":
        q = q.filter_by(is_read=True)
    if tag:
        q = q.join(Link.tags).filter(Tag.name == tag)

    total = q.count()
    total_pages = max(1, (total + LINKS_PER_PAGE - 1) // LINKS_PER_PAGE)
    page = min(max(1, page), total_pages)
    links = (
        q.order_by(Link.created_at.desc())
        .offset((page - 1) * LINKS_PER_PAGE)
        .limit(LINKS_PER_PAGE)
        .all()
    )
    base_qs = urlencode({k: v for k, v in
                         {"folder_id": folder_id or "", "show": show if show != "all" else "",
                          "tag": tag}.items() if v})
    current_folder = db.query(LinkFolder).filter_by(id=folder_id, user_id=user.id).first() if folder_id else None

    # Build per-link public share map
    from app.models.share import Share
    link_ids = [l.id for l in links]
    link_shares: dict = {}
    if link_ids:
        for s in db.query(Share).filter(
            Share.resource_type == "link",
            Share.is_public == True,
            Share.resource_id.in_(link_ids),
            Share.expires_at > datetime.utcnow(),
        ).all():
            link_shares[s.resource_id] = s

    return templates.TemplateResponse("links/list.html", {
        "request": request,
        "user": user,
        "folders": folders,
        "links": links,
        "current_folder": current_folder,
        "folder_id": folder_id,
        "show": show,
        "tag": tag,
        "link_shares": link_shares,
        "page": page,
        "total_pages": total_pages,
        "total": total,
        "base_qs": base_qs,
    })


# ---------- Bulk actions ----------

@router.post("/bulk")
def links_bulk(
    action: str = Form(...),
    ids: List[int] = Form(default=[]),
    folder_id: int = Form(0),
    tag: str = Form(""),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    links = db.query(Link).filter(Link.user_id == user.id, Link.id.in_(ids)).all() if ids else []
    now = datetime.utcnow()
    for link in links:
        if action == "read":
            link.is_read = True
            link.read_at = now
        elif action == "unread":
            link.is_read = False
            link.read_at = None
        elif action == "move":
            if folder_id:
                folder = db.query(LinkFolder).filter_by(id=folder_id, user_id=user.id).first()
                link.folder_id = folder.id if folder else link.folder_id
            else:
                link.folder_id = None
        elif action == "tag" and tag.strip():
            new_tags = get_or_create_tags(parse_tag_names(tag), user.id, db)
            have = {t.id for t in link.tags}
            for t in new_tags:
                if t.id not in have:
                    link.tags.append(t)
        elif action == "delete":
            link.deleted_at = datetime.utcnow()
    db.commit()
    return RedirectResponse("/links", status_code=302)


# ---------- Fetch metadata (AJAX) ----------

@router.get("/fetch-meta")
def fetch_meta_endpoint(
    url: str,
    user: User = Depends(get_current_user),
):
    return fetch_meta_only(url)


# ---------- Add link ----------

@router.get("/new", response_class=HTMLResponse)
def add_link_page(
    request: Request,
    folder_id: int = 0,
    url: str = "",
    title: str = "",
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    folders = db.query(LinkFolder).filter_by(user_id=user.id).order_by(LinkFolder.sort_order, LinkFolder.name).all()
    return templates.TemplateResponse("links/form.html", {
        "request": request,
        "user": user,
        "folders": folders,
        "link": None,
        "prefill_url": url,
        "prefill_title": title,
        "default_folder_id": folder_id,
        "tags_str": "",
        "error": None,
    })


@router.post("/new")
def add_link(
    request: Request,
    title: str = Form(""),
    url: str = Form(...),
    description: str = Form(""),
    folder_id: int = Form(0),
    tags: str = Form(""),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if not url.startswith(("http://", "https://")):
        url = "https://" + url

    # Auto-fetch metadata and content
    fetched = fetch_link_data(url)
    final_title = title.strip() or fetched.get("title") or url
    final_description = description.strip() or fetched.get("description") or ""
    content = fetched.get("content")

    link = Link(
        title=final_title,
        url=url.strip(),
        description=final_description,
        folder_id=folder_id or None,
        user_id=user.id,
        content_fetched_at=datetime.utcnow() if content else None,
    )
    db.add(link)
    db.commit()
    db.refresh(link)
    _save_content(link.id, content)
    link.word_count = len((content or "").split())
    set_tags_from_string(link, tags, user.id, db)
    db.commit()
    return RedirectResponse(f"/links?folder_id={folder_id}", status_code=302)


# ---------- Re-fetch content ----------

@router.post("/{link_id}/refetch")
def refetch_link(
    link_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    link = _get_link_or_404(link_id, user.id, db)
    fetched = fetch_link_data(link.url)
    if not link.title or link.title == link.url:
        link.title = fetched.get("title") or link.title
    if not link.description:
        link.description = fetched.get("description") or ""
    if fetched.get("content"):
        _save_content(link.id, fetched["content"])
        link.content_fetched_at = datetime.utcnow()
        link.word_count = len(fetched["content"].split())
    db.commit()
    folder_id = link.folder_id or 0
    return RedirectResponse(f"/links?folder_id={folder_id}", status_code=302)


# ---------- In-app reader ----------

@router.get("/{link_id}/read", response_class=HTMLResponse)
def read_link(
    link_id: int,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    link = _get_link_or_404(link_id, user.id, db)
    return templates.TemplateResponse("links/reader.html", {
        "request": request,
        "user": user,
        "link": link,
    })


@router.post("/{link_id}/progress")
async def save_link_progress(
    link_id: int,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    link = _get_link_or_404(link_id, user.id, db)
    body = await request.json()
    try:
        p = float(body.get("progress", 0))
    except (TypeError, ValueError):
        p = 0.0
    link.read_progress = min(1.0, max(0.0, p))
    db.commit()
    return JSONResponse({"ok": True})


# ---------- Edit link ----------

@router.get("/{link_id}/edit", response_class=HTMLResponse)
def edit_link_page(
    link_id: int,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    link = _get_link_or_404(link_id, user.id, db)
    folders = db.query(LinkFolder).filter_by(user_id=user.id).order_by(LinkFolder.sort_order, LinkFolder.name).all()
    return templates.TemplateResponse("links/form.html", {
        "request": request,
        "user": user,
        "folders": folders,
        "link": link,
        "prefill_url": "",
        "prefill_title": "",
        "default_folder_id": link.folder_id or 0,
        "tags_str": tags_to_string(link),
        "error": None,
    })


@router.post("/{link_id}/edit")
def edit_link(
    link_id: int,
    title: str = Form(...),
    url: str = Form(...),
    description: str = Form(""),
    folder_id: int = Form(0),
    tags: str = Form(""),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    link = _get_link_or_404(link_id, user.id, db)
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    link.title = title.strip()
    link.url = url.strip()
    link.description = description.strip()
    link.folder_id = folder_id or None
    set_tags_from_string(link, tags, user.id, db)
    db.commit()
    return RedirectResponse(f"/links?folder_id={folder_id}", status_code=302)


# ---------- Toggle read ----------

@router.post("/{link_id}/toggle-read")
def toggle_read(
    link_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    link = _get_link_or_404(link_id, user.id, db)
    link.is_read = not link.is_read
    link.read_at = datetime.utcnow() if link.is_read else None
    db.commit()
    folder_id = link.folder_id or 0
    return RedirectResponse(f"/links?folder_id={folder_id}", status_code=302)


# ---------- Delete link ----------

@router.post("/{link_id}/delete")
def delete_link(
    link_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    link = _get_link_or_404(link_id, user.id, db)
    folder_id = link.folder_id or 0
    link.deleted_at = datetime.utcnow()
    db.commit()
    return RedirectResponse(f"/links?folder_id={folder_id}", status_code=302)


# ---------- Individual link sharing ----------

@router.post("/{link_id}/share")
def share_link_public(
    link_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    link = _get_link_or_404(link_id, user.id, db)
    from app.models.share import Share
    share = _get_public_link_share(link_id, db)
    if not share:
        share = Share(owner_id=user.id, resource_type="link", resource_id=link_id, is_public=True)
        db.add(share)
        db.commit()
    return RedirectResponse(f"/links?folder_id={link.folder_id or 0}", status_code=302)


@router.post("/{link_id}/unshare")
def unshare_link_public(
    link_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    link = _get_link_or_404(link_id, user.id, db)
    from app.models.share import Share
    share = db.query(Share).filter(
        Share.resource_type == "link",
        Share.resource_id == link_id,
        Share.owner_id == user.id,
        Share.is_public == True,
    ).first()
    if share:
        db.delete(share)
        db.commit()
    return RedirectResponse(f"/links?folder_id={link.folder_id or 0}", status_code=302)


@router.get("/{link_id}/share-user", response_class=HTMLResponse)
def share_link_user_form(
    link_id: int,
    request: Request,
    error: str = "",
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    link = _get_link_or_404(link_id, user.id, db)
    from app.models.share import Share
    internal_shares = db.query(Share).filter(
        Share.resource_type == "link",
        Share.resource_id == link_id,
        Share.is_public == False,
        Share.expires_at > datetime.utcnow(),
    ).all()
    return templates.TemplateResponse("links/share_user.html", {
        "request": request, "user": user, "link": link,
        "internal_shares": internal_shares, "error": error,
    })


@router.post("/{link_id}/share-with-user")
def share_link_with_user(
    link_id: int,
    username: str = Form(...),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    link = _get_link_or_404(link_id, user.id, db)
    from app.models.user import User as UserModel
    target = db.query(UserModel).filter_by(username=username.strip()).first()
    if not target:
        return RedirectResponse(f"/links/{link_id}/share-user?error=not_found", status_code=302)
    if target.id == user.id:
        return RedirectResponse(f"/links/{link_id}/share-user?error=self", status_code=302)
    from app.models.share import Share
    existing = db.query(Share).filter(
        Share.resource_type == "link",
        Share.resource_id == link_id,
        Share.is_public == False,
        Share.shared_with_user_id == target.id,
        Share.expires_at > datetime.utcnow(),
    ).first()
    if not existing:
        share = Share(
            owner_id=user.id, resource_type="link", resource_id=link_id,
            is_public=False, shared_with_user_id=target.id,
        )
        db.add(share)
        db.commit()
    return RedirectResponse(f"/links?folder_id={link.folder_id or 0}", status_code=302)


@router.post("/{link_id}/revoke-user-share")
def revoke_link_user_share(
    link_id: int,
    share_id: int = Form(...),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _get_link_or_404(link_id, user.id, db)
    from app.models.share import Share
    share = db.query(Share).filter_by(id=share_id, owner_id=user.id).first()
    if share:
        db.delete(share)
        db.commit()
    return RedirectResponse(f"/links/{link_id}/share-user", status_code=302)


# ---------- Folders ----------

@router.get("/folders/new", response_class=HTMLResponse)
def add_folder_page(
    request: Request,
    user: User = Depends(get_current_user),
):
    return templates.TemplateResponse("links/folder_form.html", {
        "request": request,
        "user": user,
        "folder": None,
        "error": None,
    })


@router.post("/folders/new")
def add_folder(
    name: str = Form(...),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    folder = LinkFolder(name=name.strip(), user_id=user.id)
    db.add(folder)
    db.commit()
    return RedirectResponse(f"/links?folder_id={folder.id}", status_code=302)


@router.get("/folders/{folder_id}/edit", response_class=HTMLResponse)
def edit_folder_page(
    folder_id: int,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    folder = _get_folder_or_404(folder_id, user.id, db)
    return templates.TemplateResponse("links/folder_form.html", {
        "request": request,
        "user": user,
        "folder": folder,
        "error": None,
    })


@router.post("/folders/{folder_id}/edit")
def edit_folder(
    folder_id: int,
    name: str = Form(...),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    folder = _get_folder_or_404(folder_id, user.id, db)
    folder.name = name.strip()
    db.commit()
    return RedirectResponse(f"/links?folder_id={folder_id}", status_code=302)


@router.post("/folders/{folder_id}/delete")
def delete_folder(
    folder_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    folder = _get_folder_or_404(folder_id, user.id, db)
    from app.models.feed import Feed
    db.query(Feed).filter_by(folder_id=folder_id).update({Feed.folder_id: None})
    db.delete(folder)
    db.commit()
    return RedirectResponse("/links", status_code=302)


@router.get("/folders/{folder_id}/share-user", response_class=HTMLResponse)
def share_folder_user_form(
    folder_id: int,
    request: Request,
    error: str = "",
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    folder = _get_folder_or_404(folder_id, user.id, db)
    from app.models.share import Share
    internal_shares = db.query(Share).filter(
        Share.resource_type == "link_folder",
        Share.resource_id == folder_id,
        Share.is_public == False,
        Share.expires_at > datetime.utcnow(),
    ).all()
    return templates.TemplateResponse("links/folder_share_user.html", {
        "request": request, "user": user, "folder": folder,
        "internal_shares": internal_shares, "error": error,
    })


@router.post("/folders/{folder_id}/share-with-user")
def share_folder_with_user(
    folder_id: int,
    username: str = Form(...),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _get_folder_or_404(folder_id, user.id, db)  # проверка владельца
    from app.models.user import User as UserModel
    target = db.query(UserModel).filter_by(username=username.strip()).first()
    if not target:
        return RedirectResponse(f"/links/folders/{folder_id}/share-user?error=not_found", status_code=302)
    if target.id == user.id:
        return RedirectResponse(f"/links/folders/{folder_id}/share-user?error=self", status_code=302)
    from app.models.share import Share
    existing = db.query(Share).filter(
        Share.resource_type == "link_folder",
        Share.resource_id == folder_id,
        Share.is_public == False,
        Share.shared_with_user_id == target.id,
        Share.expires_at > datetime.utcnow(),
    ).first()
    if not existing:
        db.add(Share(
            owner_id=user.id, resource_type="link_folder", resource_id=folder_id,
            is_public=False, shared_with_user_id=target.id,
        ))
        db.commit()
    return RedirectResponse(f"/links?folder_id={folder_id}", status_code=302)


@router.post("/folders/{folder_id}/revoke-user-share")
def revoke_folder_user_share(
    folder_id: int,
    share_id: int = Form(...),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _get_folder_or_404(folder_id, user.id, db)
    from app.models.share import Share
    share = db.query(Share).filter_by(id=share_id, owner_id=user.id).first()
    if share:
        db.delete(share)
        db.commit()
    return RedirectResponse(f"/links/folders/{folder_id}/share-user", status_code=302)
