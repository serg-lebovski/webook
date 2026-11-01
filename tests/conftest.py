"""Общая настройка тестов: временная SQLite и временные каталоги данных.

Переменные окружения выставляются ДО импорта приложения — config.py читает их
при импорте. Сеть не используется: перевод (Google/MyMemory) подменён словарём.
Запуск:  python -m pytest -q
"""
import os
import sys
import tempfile
from pathlib import Path

_TMP = Path(tempfile.mkdtemp(prefix="webook_tests_"))
os.environ.update({
    "DATABASE_URL": f"sqlite:///{_TMP}/test.db",
    "BOOKS_DIR": str(_TMP / "books"), "COVERS_DIR": str(_TMP / "files"),
    "LINKS_DIR": str(_TMP / "links"), "AUDIOBOOKS_DIR": str(_TMP / "audiobooks"),
    "MANGA_DIR": str(_TMP / "manga"), "LOGS_DIR": str(_TMP / "logs"),
    "STAGING_DIR": str(_TMP / "staging"), "SECRET_KEY": "test-secret-key-" + "x" * 32,
    "LOG_LEVEL": "WARNING", "COOKIE_SECURE": "false",
})
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest  # noqa: E402

FAKE_TRANSLATIONS = {
    # текст → (перевод, язык, который «определил» Google)
    "The Way of Kings": ("Путь королей", "en"),
    "Brandon Sanderson": ("Брэндон Сандерсон", "en"),
    "The Stormlight Archive": ("Архив Буресвета", "en"),
    "Nochnoy dozor": ("Nochnoy dozor", "ru"),          # транслит: Google возвращает как есть
    "Sergey Lukyanenko": ("Sergey Lukyanenko", "ru"),
    "Dozory": ("Dozory", "ru"),
}


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    from app.services import book_meta_service
    monkeypatch.setattr(book_meta_service, "translate_to_ru",
                        lambda text: FAKE_TRANSLATIONS.get((text or "").strip()))


@pytest.fixture(scope="session", autouse=True)
def _db():
    """Схема БД и каталоги — до любых тестов (иначе таблиц нет, пока не стартовал TestClient)."""
    for key in ("BOOKS_DIR", "COVERS_DIR", "LINKS_DIR", "AUDIOBOOKS_DIR", "MANGA_DIR", "LOGS_DIR", "STAGING_DIR"):
        Path(os.environ[key]).mkdir(parents=True, exist_ok=True)
    from app.database import init_db
    init_db()


@pytest.fixture(scope="session")
def app():
    from app.main import app as fastapi_app
    return fastapi_app


@pytest.fixture(scope="session")
def _client(app):
    from fastapi.testclient import TestClient
    with TestClient(app) as c:
        yield c


def _make_user(username: str, password: str = "secret123", admin: bool = False):
    from app.database import SessionLocal
    from app.models.user import User
    from app.services.auth_service import hash_password
    db = SessionLocal()
    try:
        u = db.query(User).filter_by(username=username).first()
        if not u:
            u = User(username=username, password_hash=hash_password(password), is_admin=admin)
            db.add(u)
            db.commit()
            db.refresh(u)
        return u.id
    finally:
        db.close()


@pytest.fixture
def make_client(app):
    """Фабрика клиентов, залогиненных под отдельными пользователями."""
    from fastapi.testclient import TestClient
    clients = []

    def factory(username: str, admin: bool = False):
        _make_user(username, admin=admin)
        c = TestClient(app)
        c.__enter__()
        r = c.post("/login", data={"username": username, "password": "secret123"}, follow_redirects=False)
        assert r.status_code == 302 and c.cookies.get("access_token"), "login failed"
        clients.append(c)
        return c

    yield factory
    for c in clients:
        c.__exit__(None, None, None)


def fb2(title, first="Иван", last="Петров", seq=None, num=None, genres=("sf_fantasy",),
        ann="Аннотация.", enc="utf-8", keywords=""):
    seq_xml = f'<sequence name="{seq}" number="{num}"/>' if seq else ""
    g = "".join(f"<genre>{x}</genre>" for x in genres)
    kw = f"<keywords>{keywords}</keywords>" if keywords else ""
    x = f'''<?xml version="1.0" encoding="{enc}"?>
<FictionBook xmlns="http://www.gribuser.ru/xml/fictionbook/2.0" xmlns:l="http://www.w3.org/1999/xlink">
<description><title-info>{g}<author><first-name>{first}</first-name><last-name>{last}</last-name></author>
<book-title>{title}</book-title><annotation><p>{ann}</p></annotation>{kw}<date>2015</date><lang>ru</lang>{seq_xml}</title-info></description>
<body><title><p>{title}</p></title><section><title><p>Глава 1</p></title><p>Текст главы один.</p></section>
<section><title><p>Глава 2</p></title><p>Текст главы два.</p></section></body></FictionBook>'''
    return x.encode(enc)


@pytest.fixture
def fb2_factory():
    return fb2
