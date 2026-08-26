from fastapi import APIRouter, Depends, Form, Request, UploadFile, File
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
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

    from app.models.book import Book
    standalone = (
        db.query(Book)
        .filter(Book.author_id == author_id, Book.user_id == user.id, Book.series_id == None)  # noqa: E711
        .order_by(Book.title)
        .all()
    )

    return templates.TemplateResponse(
        "authors/detail.html",
        {"request": request, "user": user, "author": author, "standalone": standalone},
    )


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
