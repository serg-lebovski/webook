"""Статус цикла: прочитан / читаю / заброшен (7 дней без активности) / не начат."""
from datetime import datetime, timedelta

from app.services.series_status import status

NOW = datetime(2026, 11, 1, 12, 0)


def vol(i, read=False, read_at=None):
    return {"id": i, "is_read": read, "read_at": read_at}


def test_all_read_is_done():
    s = status([vol(1, True, NOW - timedelta(days=60)), vol(2, True, NOW - timedelta(days=50))], {}, NOW)
    assert s["code"] == "done" and s["label"] == "цикл прочитан"


def test_not_started():
    assert status([vol(1), vol(2)], {}, NOW)["code"] == "new"


def test_reading_within_7_days():
    s = status([vol(1, True, NOW - timedelta(days=10)), vol(2)], {2: (0.3, NOW - timedelta(days=2))}, NOW)
    assert s["code"] == "reading" and s["days"] == 2


def test_abandoned_after_7_days():
    s = status([vol(1, True, NOW - timedelta(days=9)), vol(2)], {}, NOW)
    assert s["code"] == "abandoned" and s["days"] == 9
    s = status([vol(1), vol(2)], {1: (0.4, NOW - timedelta(days=8))}, NOW)
    assert s["code"] == "abandoned"


def test_exactly_7_days_still_reading():
    assert status([vol(1, True, NOW - timedelta(days=7)), vol(2)], {}, NOW)["code"] == "reading"


def test_read_without_date_counts_as_abandoned():
    assert status([vol(1, True, None), vol(2)], {}, NOW)["code"] == "abandoned"


def test_landing_shows_status_chips(make_client, fb2_factory):
    from tests.test_upload_and_pages import _commit, _stage
    c = make_client("statususer")
    cards = []
    for i in (1, 2):
        cards += _stage(c, f"s{i}.fb2", fb2_factory(f"Свиток {i}", "Пётр", "Смирнов", "Свитки", i))["items"]
    ids = _commit(c, cards).json()["ids"]
    c.post(f"/books/{ids[0]}/toggle-read")
    html = c.get("/books").text
    assert 'data-status="reading"' in html and "Заброшены" in html
    for bid in ids[1:]:
        c.post(f"/books/{bid}/toggle-read")
    assert 'data-status="done"' in c.get("/books").text
    from app.database import SessionLocal
    from app.models.book import Book
    db = SessionLocal()
    sid = db.get(Book, ids[0]).series_id
    db.close()
    assert "цикл прочитан" in c.get(f"/series/{sid}").text
