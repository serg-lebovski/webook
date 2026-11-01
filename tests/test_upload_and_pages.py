"""Загрузка в два шага, страницы книги/цикла/автора/полок, безопасность, API."""
import io
import zipfile


def _stage(c, name, data, translate="1"):
    r = c.post("/books/stage", files={"file": (name, data)}, data={"translate": translate})
    assert r.status_code == 200, r.text
    return r.json()


def _commit(c, cards, **override):
    items = [{"token": x["token"], "title": x["title"], "author": x["author"], "series": x["series"],
              "series_order": x["series_order"], "year": x["year"], "tags": ", ".join(x["tags"]),
              "description": x["description"], **override} for x in cards]
    return c.post("/books/stage/commit", json={"items": items})


def test_stage_zip_commit_and_duplicates(make_client, fb2_factory):
    c = make_client("uploader")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("way.fb2", fb2_factory("The Way of Kings", "Brandon", "Sanderson", "The Stormlight Archive", 1))
        z.writestr("dozor.fb2", fb2_factory("Nochnoy dozor", "Sergey", "Lukyanenko", "Dozory", 1))
        z.writestr("heart.fb2", fb2_factory("Сердце отваги. Книга 2", "allig", "eri"))
    d = _stage(c, "books.zip", buf.getvalue())
    by_file = {x["filename"]: x for x in d["items"]}
    assert by_file["way.fb2"]["title"] == "Путь королей"
    assert by_file["way.fb2"]["series"] == "Архив Буресвета"
    assert by_file["dozor.fb2"]["author"] == "Сергей Лукьяненко"
    assert (by_file["heart.fb2"]["series"], by_file["heart.fb2"]["series_order"]) == ("Сердце отваги", 2.0)
    assert by_file["heart.fb2"]["author"] == "allig eri"

    r = _commit(c, d["items"])
    assert r.status_code == 200 and r.json()["created"] == 3
    assert _commit(c, d["items"]).status_code == 400          # токены одноразовые

    again = _stage(c, "books.zip", buf.getvalue())
    assert all(x["duplicate_of"] for x in again["items"])


def test_staged_token_is_private(make_client, fb2_factory):
    owner, other = make_client("owner1"), make_client("intruder1")
    card = _stage(owner, "x.fb2", fb2_factory("Приватная"))["items"][0]
    assert other.get(f"/books/stage/{card['token']}/cover").status_code == 404
    assert _commit(other, [card]).status_code == 400
    assert _commit(owner, [card]).status_code == 200


def test_unsupported_format(make_client):
    c = make_client("fmt")
    d = _stage(c, "x.docx", b"123")
    assert not d["items"] and "не поддерживается" in d["errors"][0]


def test_tags_case_insensitive(make_client, fb2_factory):
    c = make_client("tagger")
    a = _stage(c, "a.fb2", fb2_factory("Книга А"))["items"]
    b = _stage(c, "b.fb2", fb2_factory("Книга Б"))["items"]
    assert _commit(c, a, tags="Фэнтези").status_code == 200
    assert _commit(c, b, tags="фэнтези").status_code == 200
    from app.database import SessionLocal
    from app.models.tag import Tag
    from app.models.user import User
    db = SessionLocal()
    uid = db.query(User).filter_by(username="tagger").first().id
    assert db.query(Tag).filter(Tag.user_id == uid).count() == 1
    db.close()


def _library(c, fb2_factory):
    cards = []
    for i in (1, 2, 3):
        cards += _stage(c, f"v{i}.fb2", fb2_factory(f"Том {i}", "Анна", "Иванова", "Хроники", i))["items"]
    cards += _stage(c, "solo.fb2", fb2_factory("Одиночка", "Анна", "Иванова"))["items"]
    ids = _commit(c, cards).json()["ids"]
    return ids


def test_pages_series_and_author(make_client, fb2_factory):
    c = make_client("reader")
    ids = _library(c, fb2_factory)
    assert c.get("/books").status_code == 200
    landing = c.get("/books").text
    assert "Хроники" in landing and "Вне циклов" in landing

    grouped = c.get("/books?type=book").text
    assert 'data-kind="series"' in grouped and grouped.count('data-kind="book"') == 1
    flat = c.get("/books?type=book&flat=1").text
    assert 'data-kind="series"' not in flat and flat.count('data-kind="book"') == 4
    assert "Одиночка" in c.get("/books?no_series=1").text

    page = c.get(f"/books/{ids[0]}")
    assert page.status_code == 200 and "bp-hero" in page.text
    ins = c.get(f"/books/{ids[0]}/insights").json()
    assert [t["goto"] for t in ins["toc"]][:2] == ["t0", "t1"] and ins["chars"] > 0
    assert c.get(f"/books/{ids[0]}/read?goto=t1").status_code == 200

    from app.database import SessionLocal
    from app.models.book import Book
    db = SessionLocal()
    sid = db.get(Book, ids[0]).series_id
    aid = db.get(Book, ids[0]).author_id
    db.close()
    s = c.get(f"/series/{sid}")
    assert s.status_code == 200 and s.text.count("/books/" + str(ids[0]) + "/read") >= 1
    z = c.get(f"/series/{sid}/download")
    assert z.status_code == 200
    names = zipfile.ZipFile(io.BytesIO(z.content)).namelist()
    assert len(names) == 3 and all(n.endswith(".fb2") for n in names)
    assert c.get(f"/authors/{aid}").status_code == 200


def test_series_page_hides_other_users_books(make_client, fb2_factory):
    a, b = make_client("alice"), make_client("bob")
    ids_a = _library(a, fb2_factory)
    _library(b, fb2_factory)
    from app.database import SessionLocal
    from app.models.book import Book
    db = SessionLocal()
    sid = db.get(Book, ids_a[0]).series_id
    db.close()
    html = a.get(f"/series/{sid}").text
    for bid in ids_a[:3]:
        assert f"/books/{bid}\"" in html
    # у Боба другие id — на странице цикла Алисы их нет (Series общий, книги — нет)
    assert html.count('class="wb-row"') == 3


def test_public_storage_removed_and_photo_requires_login(app, make_client):
    from fastapi.testclient import TestClient
    anon = TestClient(app)
    assert anon.get("/storage/books/whatever.fb2").status_code == 404
    r = anon.get("/authors/1/photo", follow_redirects=False)
    assert r.status_code in (302, 401, 404) and r.status_code != 200


def test_highlights_page_with_quotes(make_client, fb2_factory):
    c = make_client("quoter")
    ids = _library(c, fb2_factory)
    c.post("/highlights", json={"resource_type": "book", "resource_id": ids[0], "quote": "Цитата", "location": "x"})
    assert c.get("/highlights").status_code == 200


def test_purge_user_with_bookmarks_and_push(make_client, fb2_factory):
    admin = make_client("boss", admin=True)
    victim = make_client("victim")
    ids = _library(victim, fb2_factory)
    from app.database import SessionLocal
    from app.models.bookmark import Bookmark
    from app.models.push_subscription import PushSubscription
    from app.models.user import User
    from app.routers.admin import _purge_user_data
    db = SessionLocal()
    uid = db.query(User).filter_by(username="victim").first().id
    db.add(Bookmark(user_id=uid, resource_type="book", resource_id=ids[0], location="1", label="x"))
    db.add(PushSubscription(user_id=uid, endpoint="https://push.example/1", p256dh="k", auth="a"))
    db.commit()
    _purge_user_data(uid, db)
    db.commit()
    assert db.query(Bookmark).filter_by(user_id=uid).count() == 0
    assert db.query(PushSubscription).filter_by(user_id=uid).count() == 0
    db.close()
    assert admin.get("/admin").status_code == 200


def test_api_upload_fills_series(app, make_client, fb2_factory):
    make_client("apiuser")
    from fastapi.testclient import TestClient
    c = TestClient(app)
    tok = c.post("/api/token", json={"username": "apiuser", "password": "secret123"}).json()["access_token"]
    r = c.post("/api/books/upload", files={"file": ("x.fb2", fb2_factory("Мир игры. Том 3", genres=("sf_litrpg",)))},
               headers={"Authorization": f"Bearer {tok}"})
    assert r.status_code == 200
    from app.database import SessionLocal
    from app.models.book import Book
    db = SessionLocal()
    b = db.get(Book, r.json()["id"])
    assert b.series.name == "Мир игры" and b.series_order == 3.0 and [t.name for t in b.tags] == ["ЛитРПГ"]
    db.close()


def test_all_simple_get_pages_render(app, make_client):
    c = make_client("walker", admin=True)
    skip = {"/push/public-key", "/books/isbn-lookup", "/games/steam-lookup", "/games/hltb-lookup",
            "/links/fetch-meta", "/pwa/splash.png", "/logout"}
    bad = []
    for r in app.routes:
        path = getattr(r, "path", "")
        if "GET" not in (getattr(r, "methods", None) or set()) or "{" in path or path in skip:
            continue
        if path.startswith(("/api", "/opds", "/highlights/list", "/bookmarks/list")):
            continue
        resp = c.get(path, follow_redirects=False)
        if resp.status_code >= 500:
            bad.append((path, resp.status_code))
    assert not bad, bad
