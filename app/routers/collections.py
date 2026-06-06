"""Коллекции/подборки: произвольные списки из любого контента."""
from fastapi import APIRouter, Depends, Form, Request, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.dependencies import get_db, get_current_user
from app.models.user import User
from app.models.collection import Collection, CollectionItem
from app.models.book import Book
from app.models.audiobook import Audiobook
from app.models.manga import Manga
from app.models.link import Link

router = APIRouter(prefix="/collections")
templates = Jinja2Templates(directory="app/templates")

_TYPES = ("book", "audiobook", "manga", "link")
_TYPE_LABEL = {"book": "Книга", "audiobook": "Аудиокнига", "manga": "Манга", "link": "Статья"}


def _own(collection_id: int, user: User, db: Session) -> Collection:
    c = db.query(Collection).filter_by(id=collection_id, user_id=user.id).first()
    if not c:
        raise HTTPException(status_code=404, detail="Подборка не найдена")
    return c


def _resolve(rtype: str, rid: int, user: User, db: Session):
    """Возвращает dict для отображения элемента либо None (удалён/нет доступа)."""
    if rtype == "book":
        b = db.query(Book).filter_by(id=rid, user_id=user.id, deleted_at=None).first()
        if b:
            return {"type": "book", "label": "Книга", "title": b.title,
                    "subtitle": b.author.name if b.author else "", "url": f"/books/{b.id}",
                    "cover": f"/books/{b.id}/cover" if b.cover_path else None, "icon": "book"}
    elif rtype == "audiobook":
        a = db.query(Audiobook).filter_by(id=rid, user_id=user.id, deleted_at=None).first()
        if a:
            return {"type": "audiobook", "label": "Аудиокнига", "title": a.title,
                    "subtitle": a.author or "", "url": f"/audiobooks/{a.id}",
                    "cover": f"/audiobooks/{a.id}/cover" if a.cover_path else None, "icon": "headphones"}
    elif rtype == "manga":
        m = db.query(Manga).filter_by(id=rid, user_id=user.id, deleted_at=None).first()
        if m:
            return {"type": "manga", "label": "Манга", "title": m.title,
                    "subtitle": m.author or "", "url": f"/manga/{m.id}",
                    "cover": f"/manga/{m.id}/cover" if m.cover_path else None, "icon": "images"}
    elif rtype == "link":
        l = db.query(Link).filter_by(id=rid, user_id=user.id, deleted_at=None).first()
        if l:
            return {"type": "link", "label": "Статья", "title": l.title,
                    "subtitle": l.url, "url": f"/links/{l.id}/read" if l.content else l.url,
                    "cover": None, "icon": "bookmark"}
    return None


@router.get("", response_class=HTMLResponse)
@router.get("/", response_class=HTMLResponse)
def collections_list(request: Request, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    cols = db.query(Collection).filter_by(user_id=user.id).order_by(Collection.name).all()
    data = []
    for c in cols:
        covers = []
        for it in c.items:
            r = _resolve(it.resource_type, it.resource_id, user, db)
            if r and r["cover"]:
                covers.append(r["cover"])
            if len(covers) >= 4:
                break
        data.append({"c": c, "count": len(c.items), "covers": covers})
    return templates.TemplateResponse("collections/list.html", {
        "request": request, "user": user, "collections": data,
    })


@router.post("")
def create_collection(name: str = Form(...), user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    name = name.strip()
    if not name:
        return RedirectResponse("/collections", status_code=302)
    c = Collection(user_id=user.id, name=name[:150])
    db.add(c)
    db.commit()
    db.refresh(c)
    return RedirectResponse(f"/collections/{c.id}", status_code=302)


@router.get("/list.json")
def list_json(resource_type: str = "", resource_id: int = 0,
              user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Список подборок для выпадающего меню «В подборку» (+ отметка, что уже добавлено)."""
    cols = db.query(Collection).filter_by(user_id=user.id).order_by(Collection.name).all()
    have = set()
    if resource_type in _TYPES and resource_id:
        rows = (db.query(CollectionItem.collection_id)
                .join(Collection, Collection.id == CollectionItem.collection_id)
                .filter(Collection.user_id == user.id,
                        CollectionItem.resource_type == resource_type,
                        CollectionItem.resource_id == resource_id).all())
        have = {r[0] for r in rows}
    return JSONResponse([{"id": c.id, "name": c.name, "has": c.id in have} for c in cols])


def _add_item(c: Collection, rtype: str, rid: int, db: Session) -> bool:
    if rtype not in _TYPES or not rid:
        return False
    exists = db.query(CollectionItem).filter_by(
        collection_id=c.id, resource_type=rtype, resource_id=rid).first()
    if exists:
        return True
    db.add(CollectionItem(collection_id=c.id, resource_type=rtype, resource_id=rid))
    db.commit()
    return True


@router.post("/{collection_id}/add")
async def add_item(collection_id: int, request: Request,
                   user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    c = _own(collection_id, user, db)
    try:
        data = await request.json()
    except Exception:
        data = {}
    ok = _add_item(c, data.get("resource_type", ""), int(data.get("resource_id") or 0), db)
    return JSONResponse({"ok": ok})


@router.post("/create-with")
async def create_with(request: Request, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Создать подборку и сразу добавить элемент (из меню «В подборку»)."""
    try:
        data = await request.json()
    except Exception:
        data = {}
    name = (data.get("name") or "").strip()
    if not name:
        return JSONResponse({"ok": False}, status_code=400)
    c = Collection(user_id=user.id, name=name[:150])
    db.add(c)
    db.commit()
    db.refresh(c)
    _add_item(c, data.get("resource_type", ""), int(data.get("resource_id") or 0), db)
    return JSONResponse({"ok": True, "id": c.id})


@router.get("/{collection_id}", response_class=HTMLResponse)
def collection_detail(collection_id: int, request: Request,
                      user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    c = _own(collection_id, user, db)
    items = []
    for it in c.items:
        r = _resolve(it.resource_type, it.resource_id, user, db)
        if r:
            r["item_id"] = it.id
            items.append(r)
    return templates.TemplateResponse("collections/detail.html", {
        "request": request, "user": user, "collection": c, "items": items,
    })


@router.post("/{collection_id}/rename")
def rename_collection(collection_id: int, name: str = Form(...),
                      user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    c = _own(collection_id, user, db)
    if name.strip():
        c.name = name.strip()[:150]
        db.commit()
    return RedirectResponse(f"/collections/{collection_id}", status_code=302)


@router.post("/{collection_id}/delete")
def delete_collection(collection_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    c = _own(collection_id, user, db)
    db.delete(c)
    db.commit()
    return RedirectResponse("/collections", status_code=302)


@router.post("/{collection_id}/items/{item_id}/delete")
def remove_item(collection_id: int, item_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    c = _own(collection_id, user, db)
    it = db.query(CollectionItem).filter_by(id=item_id, collection_id=c.id).first()
    if it:
        db.delete(it)
        db.commit()
    return RedirectResponse(f"/collections/{collection_id}", status_code=302)
