"""Нормализация метаданных книги при загрузке.

* Английское название/автор → перевод на русский (Google Translate, публичный
  endpoint `client=gtx` без ключа; запасной — MyMemory). Best-effort: при любой
  ошибке значение остаётся как было.
* Транслит («Nochnoy dozor», «Sergey Lukyanenko») → кириллица. Распознаём так:
  Google с `sl=auto` определяет транслит как `ru` и возвращает текст без
  изменений — тогда переводим локальным обратным транслитератором. Без сети —
  эвристика `looks_like_translit()`.
* Номер в цикле из названия/имени файла: «… Книга 2», «Том 3», «Book 4»,
  «Vol. 5», «#6», «Цикл 7. Название», «(Цикл - 8)».
* Коды жанров FB2 (`sf_fantasy`) → русские названия для тегов.

Наружу ходит только `translate_to_ru` (сетевой вызов), всё остальное — чистые
функции, их легко тестировать.
"""
from __future__ import annotations

import re
import threading
import time
from typing import Optional

_CYR = re.compile(r"[А-Яа-яЁё]")
_LAT = re.compile(r"[A-Za-z]")

# ─────────────────────────── перевод ───────────────────────────

_cache: dict[str, tuple[str, str]] = {}
_cache_lock = threading.Lock()
_down_until = 0.0          # «предохранитель»: после сбоя не долбим сервис минуту
_TIMEOUT = 6.0


def _google(text: str) -> Optional[tuple[str, str]]:
    import httpx
    r = httpx.get(
        "https://translate.googleapis.com/translate_a/single",
        params={"client": "gtx", "sl": "auto", "tl": "ru", "dt": "t", "q": text},
        timeout=_TIMEOUT,
        headers={"User-Agent": "Mozilla/5.0 WeBook"},
    )
    r.raise_for_status()
    data = r.json()
    translated = "".join(part[0] for part in (data[0] or []) if part and part[0])
    detected = data[2] if len(data) > 2 and isinstance(data[2], str) else ""
    return translated.strip(), detected


def _mymemory(text: str) -> Optional[tuple[str, str]]:
    import httpx
    r = httpx.get("https://api.mymemory.translated.net/get",
                  params={"q": text, "langpair": "en|ru"}, timeout=_TIMEOUT)
    r.raise_for_status()
    t = ((r.json() or {}).get("responseData") or {}).get("translatedText") or ""
    return t.strip(), "en"


def translate_to_ru(text: str) -> Optional[tuple[str, str]]:
    """(перевод, код_исходного_языка) или None, если сервис недоступен."""
    global _down_until
    text = (text or "").strip()
    if not text:
        return None
    with _cache_lock:
        if text in _cache:
            return _cache[text]
    if time.monotonic() < _down_until:
        return None
    res = None
    for fn in (_google, _mymemory):
        try:
            res = fn(text)
            if res and res[0]:
                break
        except Exception:
            res = None
    if not res or not res[0]:
        _down_until = time.monotonic() + 60
        return None
    with _cache_lock:
        if len(_cache) > 5000:
            _cache.clear()
        _cache[text] = res
    return res


# ─────────────────────────── транслит ───────────────────────────

_VOWELS = set("aeiouy")
# порядок важен: длинные сочетания раньше коротких
_MULTI = [
    ("shch", "щ"), ("sch", "щ"), ("zh", "ж"), ("kh", "х"), ("ts", "ц"), ("tz", "ц"),
    ("ch", "ч"), ("sh", "ш"), ("yu", "ю"), ("ya", "я"), ("yo", "ё"), ("ju", "ю"),
    ("ja", "я"), ("jo", "ё"), ("ye", "е"), ("ia", "ия"), ("iu", "ию"),
]
_SINGLE = {
    "a": "а", "b": "б", "v": "в", "g": "г", "d": "д", "e": "е", "z": "з", "i": "и",
    "j": "й", "k": "к", "l": "л", "m": "м", "n": "н", "o": "о", "p": "п", "r": "р",
    "s": "с", "t": "т", "u": "у", "f": "ф", "h": "х", "c": "ц", "w": "в", "x": "кс",
    "q": "к", "y": "ы", "'": "ь", "`": "ь", "’": "ь",
}


def _translit_word(word: str) -> str:
    low = word.lower()
    out, i, n = [], 0, len(low)
    while i < n:
        ch = low[i]
        if not ch.isalpha() and ch not in "'`’":
            out.append(word[i]); i += 1
            continue
        prev = low[i - 1] if i > 0 else ""
        at_start = i == 0 or not prev.isalpha()
        # окончания: -iy/-yy/-oy → ий/ый/ой, -aya → ая
        rest = low[i:]
        if rest in ("iy", "yy", "ij") and i > 0:
            out.append("ий" if rest[0] in "ij" else "ый"); break
        if rest == "y" and prev in ("k", "g") and i > 1:      # Dostoyevsky → Достоевский
            out.append("ий"); break
        if ch == "y" and prev in _VOWELS and prev and (i + 1 == n or low[i + 1] not in _VOWELS):
            out.append("й"); i += 1
            continue
        if ch == "e" and at_start:
            out.append("э"); i += 1
            continue
        matched = False
        for lat, cyr in _MULTI:
            if low.startswith(lat, i):
                # согласная + y + гласная в середине слова → мягкий знак (Lukyanenko → Лукьяненко),
                # но «-nye»/«-ye» в конце — прилагательное (Zvezdnye → Звёздные)
                if lat[0] in "yj" and prev and prev not in _VOWELS and prev.isalpha() and not at_start:
                    if lat == "ye" and i + 2 == n:
                        cyr = "ые"
                    elif lat in ("yo", "jo"):
                        pass                       # Fyodor → Фёдор, Semyon → Семён
                    else:
                        cyr = "ь" + cyr
                out.append(cyr); i += len(lat); matched = True
                break
        if matched:
            continue
        out.append(_SINGLE.get(ch, word[i])); i += 1
    res = "".join(out)
    # регистр: Слово / СЛОВО / слово
    if word.isupper() and len(word) > 1:
        return res.upper()
    if word[:1].isupper():
        return res[:1].upper() + res[1:]
    return res


def translit_to_ru(text: str) -> str:
    return re.sub(r"[A-Za-z'`’]+", lambda m: _translit_word(m.group(0)), text or "")


_EN_WORDS = set("""
the of and a an in on to for with from by at is are was be my your his her our their
war world king queen dark night book part volume game games magic star stars lord lords
road way life death love dead blood fire ice shadow shadows house city time last first
tale tales story stories legend legends man men woman girl boy black white red blue
""".split())
_EN_HINTS = ("th", "wh", "ph", "ght", "tion", "ing", "ee", "oo", "ea", "ou", "w", "q", "x", "ck")
_TR_HINTS = ("zh", "kh", "shch", "sch", "ts", "ya", "yu", "yo", "iy", "yy", "ogo", "ykh", "ikh",
             "aya", "oye", "nye", "ov", "ev", "sk")


def looks_like_translit(text: str) -> bool:
    words = re.findall(r"[A-Za-z]+", (text or "").lower())
    if not words:
        return False
    en = tr = 0
    for w in words:
        if w in _EN_WORDS:
            en += 2
            continue
        en += sum(1 for h in _EN_HINTS if h in w)
        tr += sum(1 for h in _TR_HINTS if h in w)
        if w.endswith(("y", "iy", "oy", "ij", "a")) and len(w) > 3:
            tr += 1
    return tr > en


def is_cyrillic(text: str) -> bool:
    return bool(_CYR.search(text or ""))


def _is_latin_only(text: str) -> bool:
    return bool(_LAT.search(text or "")) and not is_cyrillic(text)


def looks_like_person_name(text: str) -> bool:
    """«Brandon Sanderson», «J. R. R. Tolkien» — да; «allig_eri», «user123» — нет."""
    words = (text or "").split()
    if not words or len(words) > 5:
        return False
    if re.search(r"[\d_@]", text):
        return False
    return all(w[:1].isupper() for w in words)


def to_russian(text: str, kind: str = "title") -> tuple[str, str]:
    """Привести латиницу к русскому. Возвращает (результат, способ):
    способ = '' (не меняли) | 'translate' | 'translit'."""
    text = (text or "").strip()
    if not text or not _is_latin_only(text):
        return text, ""
    res = translate_to_ru(text)
    if res:
        translated, detected = res
        if is_cyrillic(translated):
            return translated, ("translit" if detected == "ru" else "translate")
        # Google счёл латиницу русским (транслит) и вернул как есть → переводим буквы сами
        if detected == "ru" or looks_like_translit(text):
            return translit_to_ru(text), "translit"
        return text, ""
    if looks_like_translit(text):
        return translit_to_ru(text), "translit"
    return text, ""


# ─────────────────────── номер в цикле из названия ───────────────────────

_NUM_WORDS = {
    "первая": 1, "первый": 1, "вторая": 2, "второй": 2, "третья": 3, "третий": 3,
    "четвертая": 4, "четвёртая": 4, "четвертый": 4, "четвёртый": 4, "пятая": 5, "пятый": 5,
    "шестая": 6, "шестой": 6, "седьмая": 7, "седьмой": 7, "восьмая": 8, "восьмой": 8,
    "девятая": 9, "девятый": 9, "десятая": 10, "десятый": 10,
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8,
    "nine": 9, "ten": 10,
}
_ROMAN = {"i": 1, "ii": 2, "iii": 3, "iv": 4, "v": 5, "vi": 6, "vii": 7, "viii": 8, "ix": 9,
          "x": 10, "xi": 11, "xii": 12}
_VOL = r"(?:книга|кн\.|том|т\.|часть|ч\.|выпуск|вып\.|эпизод|book|volume|vol\.?|part|episode|#|№)"
_NUM = r"(\d+(?:[.,]\d+)?|[ivxIVX]{1,4}|[А-Яа-яЁёA-Za-z]+)"
_RE_VOL_SUFFIX = re.compile(rf"^(?P<base>.*?)[\s.,:;(\[–—-]*{_VOL}\s*{_NUM}\s*[)\]]?\s*$", re.I)
_RE_VOL_ANY = re.compile(rf"(?:^|[\s.,:;(\[–—-]){_VOL}\s*{_NUM}(?=$|[\s.,:;)\]–—-])", re.I)
_RE_WORD_VOL = re.compile(r"(?:^|[\s.,:;(\[–—-])(\w+)\s+(?:книга|том|часть)(?=$|[\s.,:;)\]–—-])", re.I)
_RE_PAREN_SERIES = re.compile(
    rf"^(?P<base>.+?)\s*[(\[](?P<series>[^()\[\]]+?)[\s.,:;–—-]*{_VOL}?\s*(?P<n>\d+(?:[.,]\d+)?|[ivxIVX]{{1,4}})\s*[)\]]\s*$",
    re.I)
_RE_LEAD_NUM = re.compile(r"^\s*(\d{1,3})\s*[.)–—-]\s+(?P<rest>\S.*)$")


def _num(token: str) -> Optional[float]:
    t = (token or "").strip().lower().replace(",", ".")
    if re.fullmatch(r"\d+(?:\.\d+)?", t):
        v = float(t)
        return v if 0 < v < 1000 else None
    if t in _ROMAN:
        return float(_ROMAN[t])
    if t in _NUM_WORDS:
        return float(_NUM_WORDS[t])
    return None


def series_from_title(title: str) -> tuple[str, Optional[float]]:
    """Найти номер тома в названии. Возвращает (база_для_названия_цикла, номер).
    «Сердце отваги. Книга 1» → («Сердце отваги», 1.0)."""
    t = (title or "").strip()
    if not t:
        return "", None
    m = _RE_PAREN_SERIES.match(t)    # «Название (Цикл #2)» / «Название [Цикл, книга 2]»
    if m and re.search(r"[^\W\d_]{2,}", m.group("series")):
        n = _num(m.group("n"))
        if n is not None:
            ser = m.group("series").strip(" .,:;-–—")
            # «Дом (часть 2)» — в скобках только слово-том, циклом служит основа
            if re.fullmatch(r"(?:книга|том|часть|выпуск|book|volume|vol\.?|part)", ser, re.I):
                return m.group("base").strip(" .,:;-–—"), n
            return ser, n
    m = _RE_VOL_SUFFIX.match(t)
    if m:
        n = _num(m.group(2))
        if n is not None:
            return m.group("base").strip(" .,:;-–—("), n
    m = _RE_VOL_ANY.search(t)
    if m:
        n = _num(m.group(1))
        if n is not None:
            base = t[:m.start()].strip(" .,:;-–—(")
            return base, n
    m = _RE_WORD_VOL.search(t)      # «… Первая книга»
    if m:
        n = _num(m.group(1))
        if n is not None:
            return t[:m.start()].strip(" .,:;-–—("), n
    return "", None


_GENERIC_NAMES = {"book", "books", "книга", "книги", "том", "volume", "vol", "part", "часть",
                  "file", "файл", "document", "doc", "new", "untitled", "без названия"}


def series_from_filename(stem: str) -> tuple[str, Optional[float]]:
    """Имена вида «Автор - Цикл 2 - Название», «Цикл_03_Название», «02. Название».
    Номер должен быть отделён от названия пробелом/#/№/скобкой, а в названии —
    хотя бы 3 буквы: иначе «r3.fb2» или «book1.fb2» превращались в цикл «r» №3."""
    s = re.sub(r"[_]+", " ", stem or "").strip()
    parts = [p.strip() for p in re.split(r"\s+[-–—]\s+", s) if p.strip()]
    for p in parts:
        m = re.match(r"^(?P<name>.*[^\d\s])(?:\s+|\s*[#№(\[]\s*)(?P<n>\d{1,3})\s*[\])]?$", p)
        if not m:
            continue
        name = m.group("name").strip(" .,-#№([")
        letters = re.findall(r"[^\W\d_]", name)
        if len(letters) >= 3 and name.lower() not in _GENERIC_NAMES:
            return name, float(m.group("n"))
    m = _RE_LEAD_NUM.match(s)
    if m:
        return "", float(m.group(1))
    return series_from_title(s)


# ─────────────────────────── жанры FB2 ───────────────────────────

FB2_GENRES = {
    "sf": "Фантастика", "sf_fantasy": "Фэнтези", "sf_heroic": "Героическая фантастика",
    "sf_epic": "Эпическая фантастика", "sf_action": "Боевая фантастика",
    "sf_space": "Космическая фантастика", "sf_social": "Социальная фантастика",
    "sf_history": "Альтернативная история", "sf_detective": "Детективная фантастика",
    "sf_cyberpunk": "Киберпанк", "sf_horror": "Ужасы", "sf_humor": "Юмористическая фантастика",
    "sf_postapocalyptic": "Постапокалипсис", "sf_stimpank": "Стимпанк", "sf_litrpg": "ЛитРПГ",
    "popadanec": "Попаданцы", "sf_fantasy_city": "Городское фэнтези", "sf_mystic": "Мистика",
    "sf_technofantasy": "Технофэнтези", "fairy_fantasy": "Сказочная фантастика",
    "dragon_fantasy": "Фэнтези про драконов", "historical_fantasy": "Историческое фэнтези",
    "magician_book": "Магическое фэнтези", "russian_fantasy": "Славянское фэнтези",
    "love_sf": "Любовная фантастика", "love_fantasy": "Любовное фэнтези",
    "det_classic": "Классический детектив", "det_police": "Полицейский детектив",
    "det_action": "Боевик", "det_irony": "Иронический детектив", "det_history": "Исторический детектив",
    "det_espionage": "Шпионский детектив", "det_crime": "Криминальный детектив",
    "det_political": "Политический детектив", "det_maniac": "Маньяки", "det_hard": "Крутой детектив",
    "detective": "Детектив", "thriller": "Триллер",
    "prose": "Проза", "prose_classic": "Классическая проза", "prose_history": "Историческая проза",
    "prose_contemporary": "Современная проза", "prose_counter": "Контркультура",
    "prose_rus_classic": "Русская классика", "prose_su_classics": "Советская классика",
    "prose_military": "Военная проза", "humor_prose": "Юмористическая проза",
    "love": "Любовные романы", "love_contemporary": "Современный любовный роман",
    "love_history": "Исторический любовный роман", "love_detective": "Остросюжетный любовный роман",
    "love_short": "Короткий любовный роман", "love_erotica": "Эротика",
    "adv_western": "Вестерн", "adv_history": "Исторические приключения", "adv_indian": "Приключения про индейцев",
    "adv_maritime": "Морские приключения", "adv_geo": "Путешествия", "adv_animal": "Природа и животные",
    "adventure": "Приключения",
    "child_tale": "Сказки", "child_verse": "Детские стихи", "child_prose": "Детская проза",
    "child_sf": "Детская фантастика", "child_det": "Детский детектив", "child_adv": "Детские приключения",
    "child_education": "Детская образовательная литература", "children": "Детская литература",
    "poetry": "Поэзия", "dramaturgy": "Драматургия",
    "antique": "Старинная литература", "antique_myths": "Мифы и легенды",
    "sci_history": "История", "sci_psychology": "Психология", "sci_culture": "Культурология",
    "sci_religion": "Религиоведение", "sci_philosophy": "Философия", "sci_politics": "Политика",
    "sci_business": "Бизнес", "sci_juris": "Юриспруденция", "sci_linguistic": "Языкознание",
    "sci_medicine": "Медицина", "sci_phys": "Физика", "sci_math": "Математика",
    "sci_chem": "Химия", "sci_biology": "Биология", "sci_tech": "Технические науки",
    "science": "Научная литература", "sci_popular": "Научпоп",
    "comp_www": "Интернет", "comp_programming": "Программирование", "comp_hard": "Компьютерное железо",
    "comp_soft": "Программы", "comp_db": "Базы данных", "comp_osnet": "ОС и сети", "computers": "Компьютеры",
    "ref_encyc": "Энциклопедии", "ref_dict": "Словари", "ref_ref": "Справочники", "ref_guide": "Руководства",
    "nonf_biography": "Биографии и мемуары", "nonf_publicism": "Публицистика",
    "nonf_criticism": "Критика", "nonfiction": "Документальная литература", "design": "Искусство и дизайн",
    "religion": "Религия", "religion_rel": "Религия", "religion_esoterics": "Эзотерика",
    "humor": "Юмор", "humor_anecdote": "Анекдоты",
    "home": "Дом и семья", "home_cooking": "Кулинария", "home_pets": "Домашние животные",
    "home_crafts": "Хобби и ремёсла", "home_health": "Здоровье", "home_garden": "Сад и огород",
    "home_sport": "Спорт", "home_sex": "Эротика и секс",
    "military_special": "Военное дело", "military_history": "Военная история",
    "network_literature": "Сетевая литература",
}


def genre_names(codes: list) -> list:
    """FB2-коды → русские названия; не-коды (уже текст, напр. из EPUB) — как есть."""
    out = []
    for c in codes or []:
        c = (c or "").strip()
        if not c:
            continue
        name = FB2_GENRES.get(c.lower())
        if not name:
            if re.fullmatch(r"[a-z0-9_]+", c):
                continue          # неизвестный служебный код — в теги не тащим
            name = c
        if name not in out:
            out.append(name)
    return out


# ─────────────────────────── сборка ───────────────────────────

def _dedup_ci(items: list) -> list:
    seen, out = set(), []
    for x in items:
        k = x.casefold()
        if k not in seen:
            seen.add(k)
            out.append(x)
    return out


def normalize(meta: dict, filename: str = "", translate: bool = True) -> dict:
    """Подготовить карточку книги для формы загрузки.

    На входе — результат `book_service.parse_book_file` (без cover_data),
    на выходе — плоский dict для фронта с исходными значениями в original_*."""
    from pathlib import Path
    stem = Path(filename).stem if filename else ""
    if stem.lower().endswith((".fb2", ".epub", ".pdf")):
        stem = Path(stem).stem

    title = (meta.get("title") or "").strip() or stem.replace("_", " ").strip()
    author = (meta.get("author") or "").strip()
    series = (meta.get("series") or "").strip()
    order = meta.get("series_order")

    # номер тома: из метаданных → из названия → из имени файла
    if order is None:
        base, n = series_from_title(title)
        if n is None and stem:
            base, n = series_from_filename(stem)
        if n is not None:
            order = n
            if not series and base:
                series = base

    out = {
        "title": title, "author": author, "series": series,
        "series_order": order,
        "description": (meta.get("description") or "").strip(),
        "language": (meta.get("language") or "").strip(),
        "year": meta.get("year"),
        "tags": _dedup_ci(genre_names(meta.get("genres") or []) + [
            k for k in (meta.get("keywords") or []) if len(k) <= 40])[:12],
        "original": {},
        "how": {},
    }

    if translate:
        for field, kind in (("title", "title"), ("author", "author"), ("series", "title")):
            val = out[field]
            if kind == "author" and not looks_like_person_name(val):
                continue      # ник («allig_eri») не переводим: Google сделает из него «Будьте здоровы»
            new, how = to_russian(val, kind)
            if how:
                out["original"][field] = val
                out["how"][field] = how
                out[field] = new
    return out
