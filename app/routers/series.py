from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.dependencies import get_db, get_current_user
from app.models.series import Series
from app.models.author import Author
from app.models.user import User

router = APIRouter(prefix="/series")
templates = Jinja2Templates(directory="app/templates")


@router.get("/search")
def search_series(author_id: int = 0, q: str = "", db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    query = db.query(Series)
    if author_id:
        query = query.filter(Series.author_id == author_id)
    if q:
        query = query.filter(Series.name.ilike(f"%{q}%"))
    results = query.limit(10).all()
    return JSONResponse([{"id": s.id, "name": s.name} for s in results])


@router.get("/new", response_class=HTMLResponse)
def new_series_form(request: Request, author_id: int = 0, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    authors = db.query(Author).order_by(Author.name).all()
    selected = db.query(Author).filter(Author.id == author_id).first() if author_id else None
    return templates.TemplateResponse(
        "series/form.html", {"request": request, "user": user, "series": None, "authors": authors, "selected_author": selected, "error": None}
    )


@router.post("/new")
def create_series(
    request: Request,
    name: str = Form(...),
    author_id: int = Form(...),
    description: str = Form(""),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    series = Series(name=name.strip(), author_id=author_id, description=description.strip())
    db.add(series)
    db.commit()
    return RedirectResponse(f"/series/{series.id}", status_code=302)


@router.get("/{series_id}", response_class=HTMLResponse)
def series_detail(series_id: int, request: Request, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    series = db.query(Series).filter(Series.id == series_id).first()
    if not series:
        return RedirectResponse("/authors", status_code=302)
    from app.models.book import Book
    books = (
        db.query(Book)
        .filter(Book.series_id == series_id, Book.user_id == user.id)
        .order_by(Book.series_order)
        .all()
    )
    return templates.TemplateResponse(
        "series/detail.html", {"request": request, "user": user, "series": series, "user_books": books}
    )


@router.get("/{series_id}/edit", response_class=HTMLResponse)
def edit_series_form(series_id: int, request: Request, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    series = db.query(Series).filter(Series.id == series_id).first()
    if not series:
        return RedirectResponse("/authors", status_code=302)
    authors = db.query(Author).order_by(Author.name).all()
    return templates.TemplateResponse(
        "series/form.html", {"request": request, "user": user, "series": series, "authors": authors, "selected_author": series.author, "error": None}
    )


@router.post("/{series_id}/edit")
def edit_series(
    series_id: int,
    name: str = Form(...),
    author_id: int = Form(...),
    description: str = Form(""),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    series = db.query(Series).filter(Series.id == series_id).first()
    if series:
        series.name = name.strip()
        series.author_id = author_id
        series.description = description.strip()
        db.commit()
    return RedirectResponse(f"/series/{series_id}", status_code=302)


@router.post("/{series_id}/delete")
def delete_series(series_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    series = db.query(Series).filter(Series.id == series_id).first()
    if series:
        author_id = series.author_id
        db.delete(series)
        db.commit()
        return RedirectResponse(f"/authors/{author_id}", status_code=302)
    return RedirectResponse("/authors", status_code=302)
