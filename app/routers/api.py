from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Header, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session
from jose import JWTError, jwt

from app.dependencies import get_db
from app.models.user import User
from app.models.link import Link, LinkFolder
from app.services.auth_service import verify_password, create_access_token
from app.services.security_service import client_ip, banned_until, record_failure, record_success
from app.logging_config import auth_log
from app.config import SECRET_KEY, ALGORITHM

router = APIRouter(prefix="/api")


def _get_api_user(
    authorization: Optional[str] = Header(default=None),
    db: Session = Depends(get_db),
) -> User:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Unauthorized")
    token = authorization[7:]
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        username: str = payload.get("sub")
        if not username:
            raise HTTPException(status_code=401, detail="Invalid token")
    except JWTError:
        raise HTTPException(status_code=401, detail="Invalid token")
    user = db.query(User).filter(User.username == username).first()
    if not user:
        raise HTTPException(status_code=401, detail="User not found")
    return user


class TokenRequest(BaseModel):
    username: str
    password: str


class FolderCreate(BaseModel):
    name: str


class LinkCreate(BaseModel):
    url: str
    title: str = ""
    folder_id: Optional[int] = None


@router.post("/token")
def api_token(body: TokenRequest, request: Request, db: Session = Depends(get_db)):
    ip = client_ip(request)
    if banned_until(ip, db):
        auth_log.warning("api token BLOCKED (banned) ip=%s username=%s", ip, body.username)
        raise HTTPException(status_code=429, detail="Слишком много неудачных попыток. Адрес временно заблокирован.")
    user = db.query(User).filter(User.username == body.username).first()
    if not user or not verify_password(body.password, user.password_hash):
        record_failure(ip, db)
        auth_log.warning("api token FAILED ip=%s username=%s", ip, body.username)
        raise HTTPException(status_code=401, detail="Неверный логин или пароль")
    record_success(ip, db)
    auth_log.info("api token OK ip=%s username=%s", ip, user.username)
    token = create_access_token(user.username)
    return {"access_token": token, "username": user.username}


@router.get("/folders")
def list_folders(
    user: User = Depends(_get_api_user),
    db: Session = Depends(get_db),
):
    folders = (
        db.query(LinkFolder)
        .filter(LinkFolder.user_id == user.id)
        .order_by(LinkFolder.sort_order, LinkFolder.name)
        .all()
    )
    return [{"id": f.id, "name": f.name} for f in folders]


@router.post("/folders", status_code=201)
def create_folder(
    body: FolderCreate,
    user: User = Depends(_get_api_user),
    db: Session = Depends(get_db),
):
    name = body.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="Название папки не может быть пустым")
    folder = LinkFolder(name=name, user_id=user.id)
    db.add(folder)
    db.commit()
    db.refresh(folder)
    return {"id": folder.id, "name": folder.name}


@router.post("/links", status_code=201)
def save_link(
    body: LinkCreate,
    user: User = Depends(_get_api_user),
    db: Session = Depends(get_db),
):
    url = body.url.strip()
    if not url:
        raise HTTPException(status_code=400, detail="URL не может быть пустым")
    link = Link(
        url=url,
        title=(body.title.strip() or url)[:500],
        user_id=user.id,
        folder_id=body.folder_id,
    )
    db.add(link)
    db.commit()
    db.refresh(link)
    return {"id": link.id, "title": link.title}
