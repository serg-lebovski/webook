from datetime import datetime
from fastapi import APIRouter, Depends, Form, Request, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.dependencies import get_db, get_current_user
from app.models.shelf import Shelf
from app.models.user import User

router = APIRouter(prefix="/shelves")
templates = Jinja2Templates(directory="app/templates")


@router.get("", response_class=HTMLResponse)
def shelves_list(request: Request, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    shelves = db.query(Shelf).filter(Shelf.user_id == user.id).order_by(Shelf.sort_order, Shelf.name).all()
    return templates.TemplateResponse("shelves/list.html", {"request": request, "user": user, "shelves": shelves})


@router.get("/new", response_class=HTMLResponse)
def new_shelf_form(request: Request, user: User = Depends(get_current_user)):
    return templates.TemplateResponse("shelves/form.html", {"request": request, "user": user, "shelf": None, "error": None})


@router.post("/new")
def create_shelf(
    request: Request,
    name: str = Form(...),
    description: str = Form(""),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if not name.strip():
        return templates.TemplateResponse(
            "shelves/form.html", {"request": request, "user": user, "shelf": None, "error": "Название обязательно"}, status_code=400
        )
    shelf = Shelf(name=name.strip(), description=description.strip(), user_id=user.id)
    db.add(shelf)
    db.commit()
    return RedirectResponse(f"/shelves/{shelf.id}", status_code=302)


@router.get("/{shelf_id}", response_class=HTMLResponse)
def shelf_detail(shelf_id: int, request: Request, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    shelf = db.query(Shelf).filter(Shelf.id == shelf_id, Shelf.user_id == user.id).first()
    if not shelf:
        return RedirectResponse("/shelves", status_code=302)

    from app.models.author import Author
    from app.models.book import Book

    authors_with_books = (
        db.query(Author)
        .join(Book, Book.author_id == Author.id)
        .filter(Book.shelf_id == shelf_id, Book.user_id == user.id)
        .distinct()
        .order_by(Author.name)
        .all()
    )
    return templates.TemplateResponse(
        "shelves/detail.html",
        {"request": request, "user": user, "shelf": shelf, "authors": authors_with_books},
    )


@router.get("/{shelf_id}/edit", response_class=HTMLResponse)
def edit_shelf_form(shelf_id: int, request: Request, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    shelf = db.query(Shelf).filter(Shelf.id == shelf_id, Shelf.user_id == user.id).first()
    if not shelf:
        return RedirectResponse("/shelves", status_code=302)
    return templates.TemplateResponse("shelves/form.html", {"request": request, "user": user, "shelf": shelf, "error": None})


@router.post("/{shelf_id}/edit")
def edit_shelf(
    shelf_id: int,
    request: Request,
    name: str = Form(...),
    description: str = Form(""),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    shelf = db.query(Shelf).filter(Shelf.id == shelf_id, Shelf.user_id == user.id).first()
    if not shelf:
        return RedirectResponse("/shelves", status_code=302)
    shelf.name = name.strip()
    shelf.description = description.strip()
    db.commit()
    return RedirectResponse(f"/shelves/{shelf_id}", status_code=302)


@router.post("/{shelf_id}/delete")
def delete_shelf(shelf_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    shelf = db.query(Shelf).filter(Shelf.id == shelf_id, Shelf.user_id == user.id).first()
    if shelf:
        db.delete(shelf)
        db.commit()
    return RedirectResponse("/shelves", status_code=302)


# ---------- Sharing with users ----------

@router.get("/{shelf_id}/share-user", response_class=HTMLResponse)
def share_shelf_user_form(
    shelf_id: int,
    request: Request,
    error: str = "",
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    shelf = db.query(Shelf).filter(Shelf.id == shelf_id, Shelf.user_id == user.id).first()
    if not shelf:
        raise HTTPException(status_code=404)
    from app.models.share import Share
    internal_shares = db.query(Share).filter(
        Share.resource_type == "shelf",
        Share.resource_id == shelf_id,
        Share.is_public == False,
        Share.expires_at > datetime.utcnow(),
    ).all()
    return templates.TemplateResponse("shelves/share_user.html", {
        "request": request, "user": user, "shelf": shelf,
        "internal_shares": internal_shares, "error": error,
    })


@router.post("/{shelf_id}/share-with-user")
def share_shelf_with_user(
    shelf_id: int,
    username: str = Form(...),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    shelf = db.query(Shelf).filter(Shelf.id == shelf_id, Shelf.user_id == user.id).first()
    if not shelf:
        raise HTTPException(status_code=404)
    target = db.query(User).filter_by(username=username.strip()).first()
    if not target:
        return RedirectResponse(f"/shelves/{shelf_id}/share-user?error=not_found", status_code=302)
    if target.id == user.id:
        return RedirectResponse(f"/shelves/{shelf_id}/share-user?error=self", status_code=302)
    from app.models.share import Share
    existing = db.query(Share).filter(
        Share.resource_type == "shelf",
        Share.resource_id == shelf_id,
        Share.is_public == False,
        Share.shared_with_user_id == target.id,
        Share.expires_at > datetime.utcnow(),
    ).first()
    if not existing:
        db.add(Share(
            owner_id=user.id, resource_type="shelf", resource_id=shelf_id,
            is_public=False, shared_with_user_id=target.id,
        ))
        db.commit()
    return RedirectResponse(f"/shelves/{shelf_id}", status_code=302)


@router.post("/{shelf_id}/revoke-user-share")
def revoke_shelf_user_share(
    shelf_id: int,
    share_id: int = Form(...),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    shelf = db.query(Shelf).filter(Shelf.id == shelf_id, Shelf.user_id == user.id).first()
    if not shelf:
        raise HTTPException(status_code=404)
    from app.models.share import Share
    share = db.query(Share).filter_by(id=share_id, owner_id=user.id).first()
    if share:
        db.delete(share)
        db.commit()
    return RedirectResponse(f"/shelves/{shelf_id}/share-user", status_code=302)


