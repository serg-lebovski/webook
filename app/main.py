import time

from fastapi import FastAPI, Depends, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from jose import JWTError, jwt
from sqlalchemy.exc import SQLAlchemyError

from app.config import BOOKS_DIR, COVERS_DIR, LINKS_CONTENT_DIR, AUDIOBOOKS_DIR, FILES_DIR, MANGA_DIR, LOGS_DIR, APP_TITLE, SECRET_KEY, ALGORITHM
from app.database import init_db
from app.dependencies import get_current_user
from app.logging_config import setup_logging, access_log, actions_log, error_log, db_log
from app.services.security_service import client_ip
from app.routers import auth, shelves, authors, series, books, opds, settings, links, admin, share, shared, dashboard, api, search, tags, highlights, feeds, pwa, audiobooks, tier_list, files, trash, manga

app = FastAPI(title=APP_TITLE, docs_url=None, redoc_url=None)


def _username_from_request(request: Request) -> str:
    """Имя пользователя из cookie-JWT без обращения к БД (для логов)."""
    token = request.cookies.get("access_token")
    if not token:
        return "-"
    try:
        return jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM]).get("sub") or "-"
    except JWTError:
        return "-"


@app.middleware("http")
async def log_requests(request: Request, call_next):
    path = request.url.path
    if path.startswith(("/static", "/storage")):
        return await call_next(request)
    start = time.monotonic()
    response = await call_next(request)
    dur_ms = (time.monotonic() - start) * 1000
    ip = client_ip(request)
    user = _username_from_request(request)
    access_log.info("%s %s -> %s %.0fms ip=%s user=%s",
                    request.method, path, response.status_code, dur_ms, ip, user)
    if request.method not in ("GET", "HEAD", "OPTIONS"):
        actions_log.info("%s %s -> %s ip=%s user=%s",
                         request.method, path, response.status_code, ip, user)
    return response

# Allow browser extension origins (chrome-extension://, moz-extension://) to call /api/*
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "POST"],
    allow_headers=["Authorization", "Content-Type"],
)

app.mount("/static", StaticFiles(directory="static"), name="static")
app.mount("/storage/books", StaticFiles(directory=str(BOOKS_DIR)), name="books_storage")
app.mount("/storage/covers", StaticFiles(directory=str(COVERS_DIR)), name="covers_storage")

app.include_router(auth.router)
app.include_router(shelves.router)
app.include_router(authors.router)
app.include_router(series.router)
app.include_router(books.router)
app.include_router(opds.router)
app.include_router(settings.router)
app.include_router(links.router)
app.include_router(admin.router)
app.include_router(share.router)
app.include_router(shared.router)
app.include_router(dashboard.router)
app.include_router(api.router)
app.include_router(search.router)
app.include_router(tags.router)
app.include_router(highlights.router)
app.include_router(feeds.router)
app.include_router(pwa.router)
app.include_router(audiobooks.router)
app.include_router(tier_list.router)
app.include_router(files.router)
app.include_router(trash.router)
app.include_router(manga.router)

templates = Jinja2Templates(directory="app/templates")


@app.on_event("startup")
def on_startup():
    BOOKS_DIR.mkdir(parents=True, exist_ok=True)
    COVERS_DIR.mkdir(parents=True, exist_ok=True)
    LINKS_CONTENT_DIR.mkdir(parents=True, exist_ok=True)
    AUDIOBOOKS_DIR.mkdir(parents=True, exist_ok=True)
    FILES_DIR.mkdir(parents=True, exist_ok=True)
    MANGA_DIR.mkdir(parents=True, exist_ok=True)
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    setup_logging()
    init_db()


@app.get("/", response_class=HTMLResponse)
def root(request: Request, user=Depends(get_current_user)):
    return RedirectResponse("/dashboard", status_code=302)


@app.exception_handler(302)
async def redirect_handler(request: Request, exc):
    return RedirectResponse(exc.headers["Location"], status_code=302)


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    """Логирует необработанные исключения: ошибки БД — в db.log, прочие — в errors.log."""
    where = f"{request.method} {request.url.path}"
    if isinstance(exc, SQLAlchemyError):
        db_log.exception("DB error on %s: %s", where, exc)
    else:
        error_log.exception("Unhandled error on %s: %s", where, exc)
    return HTMLResponse(
        "<h1>500 — внутренняя ошибка</h1><p>Что-то пошло не так. "
        "Администратор уже видит это в логах.</p>",
        status_code=500,
    )
