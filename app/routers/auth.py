from fastapi import APIRouter, Depends, Form, Request, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from app.dependencies import get_db, get_current_user_optional
from app.models.user import User
from app.services.auth_service import hash_password, verify_password, create_access_token
from app.services.settings_service import is_registration_allowed
from app.services.security_service import client_ip, banned_until, record_failure, record_success
from app.logging_config import auth_log

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")


@router.get("/login", response_class=HTMLResponse)
def login_page(
    request: Request,
    user=Depends(get_current_user_optional),
    db: Session = Depends(get_db),
):
    if user:
        return RedirectResponse("/", status_code=302)
    first_user = db.query(User).count() == 0
    can_register = first_user or is_registration_allowed(db)
    return templates.TemplateResponse("login.html", {"request": request, "error": None, "can_register": can_register})


@router.post("/login")
def login(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(get_db),
):
    ip = client_ip(request)
    until = banned_until(ip, db)
    if until:
        auth_log.warning("login BLOCKED (banned) ip=%s username=%s", ip, username)
        return templates.TemplateResponse(
            "login.html",
            {"request": request,
             "error": "Слишком много неудачных попыток входа. Доступ с вашего адреса временно заблокирован."},
            status_code=429,
        )

    user = db.query(User).filter(User.username == username).first()
    if not user or not verify_password(password, user.password_hash):
        record_failure(ip, db)
        auth_log.warning("login FAILED ip=%s username=%s", ip, username)
        return templates.TemplateResponse(
            "login.html", {"request": request, "error": "Неверный логин или пароль"}, status_code=401
        )
    record_success(ip, db)
    auth_log.info("login OK ip=%s username=%s", ip, user.username)
    token = create_access_token(user.username)
    response = RedirectResponse("/", status_code=302)
    response.set_cookie("access_token", token, httponly=True, max_age=60 * 60 * 24 * 7, samesite="lax")
    return response


@router.post("/logout")
def logout():
    response = RedirectResponse("/login", status_code=302)
    response.delete_cookie("access_token")
    return response


@router.get("/register", response_class=HTMLResponse)
def register_page(request: Request, db: Session = Depends(get_db)):
    first_user = db.query(User).count() == 0
    if not first_user and not is_registration_allowed(db):
        raise HTTPException(status_code=403, detail="Регистрация закрыта")
    return templates.TemplateResponse("register.html", {"request": request, "error": None})


@router.post("/register")
def register(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(get_db),
):
    first_user = db.query(User).count() == 0
    if not first_user and not is_registration_allowed(db):
        raise HTTPException(status_code=403, detail="Регистрация закрыта")
    if len(password) < 6:
        return templates.TemplateResponse(
            "register.html", {"request": request, "error": "Пароль минимум 6 символов"}, status_code=400
        )
    user = User(username=username, password_hash=hash_password(password), is_admin=first_user)
    db.add(user)
    db.commit()
    auth_log.info("register user=%s admin=%s ip=%s", user.username, first_user, client_ip(request))
    token = create_access_token(user.username)
    response = RedirectResponse("/", status_code=302)
    response.set_cookie("access_token", token, httponly=True, max_age=60 * 60 * 24 * 7, samesite="lax")
    return response
