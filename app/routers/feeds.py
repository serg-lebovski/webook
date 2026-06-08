import xml.etree.ElementTree as ET
from collections import defaultdict

from fastapi import APIRouter, Depends, Form, Request, UploadFile, File
from fastapi.responses import HTMLResponse, RedirectResponse, Response
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
    success: str = "",
    added: int = 0,
    skipped: int = 0,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    feeds = db.query(Feed).filter_by(user_id=user.id).order_by(Feed.created_at.desc()).all()
    folders = db.query(LinkFolder).filter_by(user_id=user.id).order_by(LinkFolder.name).all()
    return templates.TemplateResponse("feeds/index.html", {
        "request": request, "user": user, "feeds": feeds, "folders": folders,
        "success": success, "imp_added": added, "imp_skipped": skipped,
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


# ---------- OPML import / export ----------

@router.get("/export.opml")
def export_opml(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Экспорт всех подписок пользователя в OPML (с группировкой по папкам)."""
    feeds = db.query(Feed).filter_by(user_id=user.id).order_by(Feed.title).all()

    opml = ET.Element("opml", version="2.0")
    head = ET.SubElement(opml, "head")
    ET.SubElement(head, "title").text = "WeBook subscriptions"
    body = ET.SubElement(opml, "body")

    by_folder: dict = defaultdict(list)
    for f in feeds:
        by_folder[f.folder.name if f.folder else None].append(f)

    def feed_outline(parent, f):
        label = f.title or f.url
        ET.SubElement(parent, "outline", type="rss", text=label, title=label,
                      xmlUrl=f.url)

    for f in by_folder.get(None, []):
        feed_outline(body, f)
    for name, fs in by_folder.items():
        if name is None:
            continue
        folder_el = ET.SubElement(body, "outline", text=name, title=name)
        for f in fs:
            feed_outline(folder_el, f)

    xml = '<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(opml, encoding="unicode")
    return Response(
        xml,
        media_type="text/x-opml",
        headers={"Content-Disposition": 'attachment; filename="webook-feeds.opml"'},
    )


@router.post("/import")
async def import_opml(
    file: UploadFile = File(...),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Импорт подписок из OPML-файла. Папки-аутлайны → LinkFolder, дедуп по URL."""
    try:
        data = await file.read()
        root = ET.fromstring(data)
    except Exception:
        return RedirectResponse("/feeds?success=opml_error", status_code=302)

    existing = {f.url for f in db.query(Feed).filter_by(user_id=user.id).all()}
    folders = {f.name: f for f in db.query(LinkFolder).filter_by(user_id=user.id).all()}
    stats = {"added": 0, "skipped": 0}
    new_feeds: list[Feed] = []

    def get_or_create_folder(name: str):
        name = (name or "").strip()
        if not name:
            return None
        fol = folders.get(name)
        if not fol:
            fol = LinkFolder(name=name, user_id=user.id)
            db.add(fol)
            db.flush()
            folders[name] = fol
        return fol.id

    def walk(node, folder_id):
        for outline in node.findall("outline"):
            xml_url = outline.get("xmlUrl") or outline.get("xmlurl")
            if xml_url:
                url = xml_url.strip()
                if not url or url in existing:
                    stats["skipped"] += 1
                    continue
                feed = Feed(
                    user_id=user.id,
                    url=url,
                    title=(outline.get("title") or outline.get("text") or "").strip(),
                    folder_id=folder_id,
                )
                db.add(feed)
                existing.add(url)
                new_feeds.append(feed)
                stats["added"] += 1
            else:
                name = outline.get("title") or outline.get("text") or ""
                walk(outline, get_or_create_folder(name) or folder_id)

    body = root.find("body")
    if body is not None:
        walk(body, None)
    db.commit()

    # Подтягиваем содержимое новых подписок (best-effort)
    for feed in new_feeds:
        try:
            db.refresh(feed)
            refresh_feed(feed, db)
        except Exception:
            pass

    return RedirectResponse(
        f"/feeds?success=opml_import&added={stats['added']}&skipped={stats['skipped']}",
        status_code=302,
    )
