"""Подписка на Web Push уведомления."""
from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.dependencies import get_db, get_current_user
from app.models.user import User
from app.services import push_service

router = APIRouter(prefix="/push")


@router.get("/public-key")
def public_key(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return JSONResponse({"key": push_service.public_key(db)})


@router.post("/subscribe")
async def subscribe(request: Request, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    data = await request.json()
    endpoint = data.get("endpoint")
    keys = data.get("keys") or {}
    if not endpoint or not keys.get("p256dh") or not keys.get("auth"):
        return JSONResponse({"ok": False}, status_code=400)
    push_service.subscribe(db, user.id, endpoint, keys["p256dh"], keys["auth"])
    return JSONResponse({"ok": True})


@router.post("/unsubscribe")
async def unsubscribe(request: Request, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    data = await request.json()
    endpoint = data.get("endpoint")
    if endpoint:
        push_service.unsubscribe(db, endpoint)
    return JSONResponse({"ok": True})
