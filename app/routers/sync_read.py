"""Синхронное чтение вслух: одно устройство ведёт (лидер), второе следует —
текущий абзац подсвечивается/скроллится в реальном времени через WebSocket.

Первый WebSocket в проекте. Аутентификация для HTTP-роутов как обычно (cookie
JWT через get_current_user); для WS-хендшейка cookie тоже приходит автоматически
(same-origin), но Depends с редиректом на /login не годится для WS-контекста —
декодируем JWT вручную (та же логика, что в dependencies.get_current_user).
"""
from fastapi import APIRouter, Depends, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from jose import JWTError, jwt
from sqlalchemy.orm import Session

from app.config import BOOKS_DIR, SECRET_KEY, ALGORITHM
from app.database import SessionLocal
from app.dependencies import get_db, get_current_user
from app.models.user import User
from app.services import book_service, sync_reading_service
from app.routers.books import _accessible_book

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")


def _load_paragraphs(book) -> list:
    if not book.file_path:
        return []
    path = BOOKS_DIR / book.file_path
    if not path.is_file():
        return []
    return book_service.extract_book_text(path.read_bytes(), book.file_format) or []


@router.post("/books/{book_id}/sync/create")
def sync_create(book_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    book = _accessible_book(book_id, user, db)
    if not _load_paragraphs(book):
        return JSONResponse({"ok": False, "message": "Не удалось извлечь текст книги для синхронного чтения"}, status_code=422)
    code = sync_reading_service.create_room(book.id, user.id)
    return JSONResponse({"ok": True, "code": code})


@router.get("/books/{book_id}/sync", response_class=HTMLResponse)
def sync_join_page(book_id: int, request: Request, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    book = _accessible_book(book_id, user, db)
    return templates.TemplateResponse("books/sync_join.html", {"request": request, "user": user, "book": book})


@router.get("/books/{book_id}/sync/{code}", response_class=HTMLResponse)
def sync_read_page(
    book_id: int,
    code: str,
    request: Request,
    lead: int = 0,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    book = _accessible_book(book_id, user, db)
    room = sync_reading_service.get_room(code.upper())
    if not room or room["book_id"] != book.id:
        raise HTTPException(status_code=404, detail="Комната не найдена или уже закрыта")
    paragraphs = _load_paragraphs(book)
    if not paragraphs:
        raise HTTPException(status_code=422, detail="Не удалось извлечь текст книги")
    return templates.TemplateResponse("books/sync_read.html", {
        "request": request, "user": user, "book": book, "code": code.upper(),
        "is_lead": bool(lead), "paragraphs": paragraphs,
    })


def _decode_ws_user(token: str, db: Session) -> User | None:
    if not token:
        return None
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        username = payload.get("sub")
    except JWTError:
        return None
    if not username:
        return None
    return db.query(User).filter(User.username == username).first()


@router.websocket("/ws/sync/{code}")
async def sync_ws(websocket: WebSocket, code: str):
    code = code.upper()
    db = SessionLocal()
    try:
        token = websocket.cookies.get("access_token")
        user = _decode_ws_user(token, db)
        room = sync_reading_service.get_room(code)
        if not user or not room:
            await websocket.close(code=4404)
            return
        try:
            _accessible_book(room["book_id"], user, db)
        except HTTPException:
            await websocket.close(code=4403)
            return

        await websocket.accept()
        sync_reading_service.add_client(code, websocket)
        try:
            while True:
                data = await websocket.receive_json()
                index = data.get("index")
                if isinstance(index, int):
                    await sync_reading_service.broadcast(code, index, sender=websocket)
        except WebSocketDisconnect:
            pass
        except Exception:
            pass
        finally:
            sync_reading_service.remove_client(code, websocket)
    finally:
        db.close()
