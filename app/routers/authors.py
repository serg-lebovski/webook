from fastapi import APIRouter, Depends, Form, Request, UploadFile, File
from fastapi import HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse, FileResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.dependencies import get_db, get_current_user
from app.models.author import Author
from app.models.user import User
from app.services.book_service import save_cover_file, delete_file
from app.services.isbn_service import download_cover
from app.services import openlibrary_service
from app.config import COVERS_DIR

router = APIRouter(prefix="/authors")
templates = Jinja2Templates(directory="app/templates")
from app.routers.books import _humanbytes  # noqa: E402
templates.env.filters["humanbytes"] = _humanbytes


@router.get("", response_class=HTMLResponse)
def authors_list(request: Request, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    from app.models.book import Book
    authors = (
        db.query(Author)
        .join(Book, Book.author_id == Author.id)
        .filter(Book.user_id == user.id)
        .distinct()
        .order_by(Author.name)
        .all()
    )
    return templates.TemplateResponse("authors/list.html", {"request": request, "user": user, "authors": authors})


@router.get("/search")
def search_authors(q: str = "", db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    results = db.query(Author).filter(Author.name.ilike(f"%{q}%")).limit(10).all()
    return JSONResponse([{"id": a.id, "name": a.name} for a in results])


@router.get("/new", response_class=HTMLResponse)
def new_author_form(request: Request, user: User = Depends(get_current_user)):
    return templates.TemplateResponse("authors/form.html", {"request": request, "user": user, "author": None, "error": None})


@router.post("/new")
async def create_author(
    request: Request,
    name: str = Form(...),
    bio: str = Form(""),
    photo: UploadFile = File(None),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if not name.strip():
        return templates.TemplateResponse(
            "authors/form.html", {"request": request, "user": user, "author": None, "error": "Имя обязательно"}, status_code=400
        )
    photo_path = None
    if photo and photo.filename:
        suffix = "." + photo.filename.rsplit(".", 1)[-1].lower()
        data = await photo.read()
        photo_path = save_cover_file(data, suffix)

    author = Author(name=name.strip(), bio=bio.strip(), photo_path=photo_path)
    db.add(author)
    db.commit()
    return RedirectResponse(f"/authors/{author.id}", status_code=302)


@router.get("/{author_id}", response_class=HTMLResponse)
def author_detail(author_id: int, request: Request, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    author = db.query(Author).filter(Author.id == author_id).first()
    if not author:
        return RedirectResponse("/authors", status_code=302)

    from sqlalchemy.orm import joinedload
    from app.models.book import Book
    # Только свои книги не из корзины (Author/Series общие для всех пользователей).
    books = (
        db.query(Book).options(joinedload(Book.series))
        .filter(Book.author_id == author_id, Book.user_id == user.id, Book.deleted_at.is_(None))
        .order_by(Book.series_order.is_(None), Book.series_order, Book.title)
        .all()
    )
    series_map: dict = {}
    standalone = []
    for b in books:
        if b.series_id:
            series_map.setdefault(b.series_id, {"series": b.series, "books": []})["books"].append(b)
        else:
            standalone.append(b)
    series_list = sorted(series_map.values(), key=lambda x: (x["series"].name or "").lower())
    for sg in series_list:
        sg["read"] = sum(1 for x in sg["books"] if x.is_read)
        sg["cover"] = next((x for x in sg["books"] if x.cover_path), None)
    standalone.sort(key=lambda x: (x.title or "").lower())
    rated = [b.rating for b in books if b.rating]
    tags = []
    for b in books:
        for t in b.tags:
            if t.name not in tags:
                tags.append(t.name)
    reading_now = [b for b in books if not b.is_read and b.in_reading_list][:5]
    return templates.TemplateResponse("authors/detail.html", {
        "request": request, "user": user, "author": author,
        "series_list": series_list, "standalone": standalone, "books": books,
        "stats": {"books": len(books), "read": sum(1 for b in books if b.is_read),
                  "series": len(series_list), "avg": round(sum(rated) / len(rated), 1) if rated else None},
        "tags": tags[:10], "reading_now": reading_now,
        "recent": sorted(books, key=lambda x: x.added_at or x.id, reverse=True)[:5],
    })


@router.get("/{author_id}/photo")
def author_photo(author_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Фото автора — только для вошедших (раньше отдавалось открытым /storage/covers)."""
    author = db.query(Author).filter(Author.id == author_id).first()
    if not author or not author.photo_path:
        raise HTTPException(status_code=404)
    path = (COVERS_DIR / author.photo_path).resolve()
    if COVERS_DIR.resolve() not in path.parents or not path.is_file():
        raise HTTPException(status_code=404)
    return FileResponse(path, headers={"Cache-Control": "private, max-age=86400"})


@router.get("/{author_id}/edit", response_class=HTMLResponse)
def edit_author_form(author_id: int, request: Request, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    author = db.query(Author).filter(Author.id == author_id).first()
    if not author:
        return RedirectResponse("/authors", status_code=302)
    return templates.TemplateResponse("authors/form.html", {"request": request, "user": user, "author": author, "error": None})


@router.post("/{author_id}/edit")
async def edit_author(
    author_id: int,
    request: Request,
    name: str = Form(...),
    bio: str = Form(""),
    photo: UploadFile = File(None),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    author = db.query(Author).filter(Author.id == author_id).first()
    if not author:
        return RedirectResponse("/authors", status_code=302)
    author.name = name.strip()
    author.bio = bio.strip()
    if photo and photo.filename:
        suffix = "." + photo.filename.rsplit(".", 1)[-1].lower()
        data = await photo.read()
        delete_file(author.photo_path, COVERS_DIR)
        author.photo_path = save_cover_file(data, suffix)
    db.commit()
    return RedirectResponse(f"/authors/{author_id}", status_code=302)


@router.post("/{author_id}/enrich")
def enrich_author(author_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Подтягивает биографию/фото автора из Open Library (best-effort, без ключа)."""
    author = db.query(Author).filter(Author.id == author_id).first()
    if not author:
        return RedirectResponse("/authors", status_code=302)
    info = openlibrary_service.fetch_author_info(author.name)
    if info:
        if info["bio"] and not author.bio:
            author.bio = info["bio"][:4000]
        if info["photo_url"] and not author.photo_path:
            data = download_cover(info["photo_url"])
            if data:
                delete_file(author.photo_path, COVERS_DIR)
                author.photo_path = save_cover_file(data, ".jpg")
        db.commit()
    return RedirectResponse(f"/authors/{author_id}", status_code=302)


@router.post("/{author_id}/delete")
def delete_author(author_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    author = db.query(Author).filter(Author.id == author_id).first()
    if author:
        delete_file(author.photo_path, COVERS_DIR)
        db.delete(author)
        db.commit()
    return RedirectResponse("/authors", status_code=302)
