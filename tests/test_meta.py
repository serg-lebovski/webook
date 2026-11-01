"""Чистые функции нормализации метаданных (без БД и сети)."""
import pytest

from app.services import book_meta_service as m


@pytest.mark.parametrize("src,expected", [
    ("Nochnoy dozor", "Ночной дозор"),
    ("Sergey Lukyanenko", "Сергей Лукьяненко"),
    ("Zvezdnye voyny", "Звездные войны"),
    ("Shchit i mech", "Щит и меч"),
    ("Fyodor Dostoyevsky", "Фёдор Достоевский"),
    ("Igor' Nikolaev", "Игорь Николаев"),
    ("Yevgeniy Onegin", "Евгений Онегин"),
])
def test_translit(src, expected):
    assert m.translit_to_ru(src) == expected


@pytest.mark.parametrize("title,base,num", [
    ("Сердце отваги измеряется численностью. Книга 1", "Сердце отваги измеряется численностью", 1),
    ("Ведьмак. Том 3", "Ведьмак", 3),
    ("Хроники Амбера. Книга IV", "Хроники Амбера", 4),
    ("Жест Лицедея. Часть вторая", "Жест Лицедея", 2),
    ("Путь королей (Архив Буресвета #1)", "Архив Буресвета", 1),
    ("Дом (часть 2)", "Дом", 2),
    ("Harry Potter, Book 2", "Harry Potter", 2),
    ("Dune Vol. 4", "Dune", 4),
])
def test_series_from_title(title, base, num):
    assert m.series_from_title(title) == (base, float(num))


@pytest.mark.parametrize("title", ["Метро 2033", "1984", "Книга мёртвых", "Город (2020)", "Ночной дозор"])
def test_series_from_title_negative(title):
    assert m.series_from_title(title)[1] is None


@pytest.mark.parametrize("stem,base,num", [
    ("Лукьяненко - Дозоры 2 - Дневной дозор", "Дозоры", 2),
    ("Wheel of Time 07", "Wheel of Time", 7),
    ("02. Дневной дозор", "", 2),
])
def test_series_from_filename(stem, base, num):
    assert m.series_from_filename(stem) == (base, float(num))


def test_series_from_filename_year_is_not_volume():
    assert m.series_from_filename("Metro 2033")[1] is None


def test_person_name_detection():
    assert m.looks_like_person_name("Brandon Sanderson")
    assert m.looks_like_person_name("J. R. R. Tolkien")
    assert not m.looks_like_person_name("allig eri")
    assert not m.looks_like_person_name("allig_eri")


def test_genres_and_tag_dedup():
    card = m.normalize({"title": "X", "genres": ["sf_fantasy", "unknown_code", "Fantasy"],
                        "keywords": ["фэнтези", "магия"]}, "x.fb2", translate=False)
    assert card["tags"] == ["Фэнтези", "Fantasy", "магия"]


def test_normalize_translates_english_and_translit():
    card = m.normalize({"title": "The Way of Kings", "author": "Brandon Sanderson",
                        "series": "The Stormlight Archive", "series_order": 1.0}, "a.fb2")
    assert (card["title"], card["author"], card["series"]) == ("Путь королей", "Брэндон Сандерсон", "Архив Буресвета")
    assert card["original"]["title"] == "The Way of Kings" and card["how"]["title"] == "translate"

    card = m.normalize({"title": "Nochnoy dozor", "author": "Sergey Lukyanenko"}, "b.fb2")
    assert card["title"] == "Ночной дозор" and card["how"]["title"] == "translit"
    assert card["author"] == "Сергей Лукьяненко"


def test_normalize_keeps_nickname_and_derives_series():
    card = m.normalize({"title": "Сердце отваги. Книга 2", "author": "allig eri"}, "a.fb2")
    assert card["author"] == "allig eri" and "author" not in card["original"]
    assert (card["series"], card["series_order"]) == ("Сердце отваги", 2.0)


def test_normalize_without_translation():
    card = m.normalize({"title": "The Way of Kings"}, "a.fb2", translate=False)
    assert card["title"] == "The Way of Kings" and not card["original"]


@pytest.mark.parametrize("stem", ["r3", "book1", "Book 2", "Книга 3", "x 5", "Metro 2033"])
def test_series_from_filename_rejects_noise(stem):
    assert m.series_from_filename(stem)[1] is None or m.series_from_filename(stem)[0] == ""


@pytest.mark.parametrize("stem,base,num", [
    ("Дозоры #2", "Дозоры", 2),
    ("Ведьмак (3)", "Ведьмак", 3),
])
def test_series_from_filename_separators(stem, base, num):
    assert m.series_from_filename(stem) == (base, float(num))
