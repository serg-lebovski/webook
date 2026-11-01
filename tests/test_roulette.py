"""Рулетка: выпадает непрочитанный цикл (один «билет» на цикл) или книга вне циклов."""
from tests.test_upload_and_pages import _commit, _stage


def _ids(c, fb2_factory, specs):
    cards = []
    for i, (title, series, num) in enumerate(specs):
        cards += _stage(c, f"r{i}.fb2", fb2_factory(title, "Ольга", "Сидорова", series, num))["items"]
    return _commit(c, cards).json()["ids"]


def test_roulette_series_or_standalone(make_client, fb2_factory):
    c = make_client("gambler")
    ids = _ids(c, fb2_factory, [("Т1", "Длинный цикл", 1), ("Т2", "Длинный цикл", 2), ("Т3", "Длинный цикл", 3),
                                ("Одна", None, None)])
    seen = set()
    for _ in range(40):
        d = c.get("/books/roulette/pick").json()
        assert d["ok"]
        seen.add(d["kind"])
        if d["kind"] == "series":
            assert d["url"].startswith("/series/") and d["next_order"] == "1" and "3 тома" in d["subtitle"]
        else:
            assert d["title"] == "Одна" and d["subtitle"] == "Книга вне циклов"
    assert seen == {"series", "book"}

    # exclude не даёт выпасть тому же варианту подряд
    first = c.get("/books/roulette/pick").json()
    for _ in range(10):
        assert c.get(f"/books/roulette/pick?exclude={first['key']}").json()["key"] != first["key"]

    # прочитанные исчезают из розыгрыша: следующий том цикла и пустой результат
    for bid in ids[:1]:
        c.post(f"/books/{bid}/toggle-read")
    d = c.get("/books/roulette/pick?exclude=book:%d" % ids[3]).json()
    assert d["kind"] == "series" and d["next_order"] == "2"
    for bid in ids[1:]:
        c.post(f"/books/{bid}/toggle-read")
    r = c.get("/books/roulette/pick")
    assert r.status_code == 404 and not r.json()["ok"]
