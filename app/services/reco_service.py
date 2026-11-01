"""Рекомендации «что почитать дальше» из собственной библиотеки пользователя.

Сигналы (по убыванию веса):
  • следующая книга в цикле, который пользователь уже читает;
  • непрочитанная книга автора, чьи книги оценены на 4–5;
  • совпадение тегов с прочитанным/избранным;
  • присутствие в списке «Хочу прочитать» / избранном.
Чисто по локальным данным, без внешних сервисов.
"""
import random
from collections import defaultdict

from sqlalchemy.orm import Session

from app.models.book import Book


def _score_candidates(db: Session, user_id: int) -> list[tuple[int, Book, str]]:
    """Скоринг непрочитанных книг пользователя. Возвращает (score, book, reason)."""
    books = (
        db.query(Book)
        .filter(Book.user_id == user_id, Book.deleted_at.is_(None))
        .all()
    )
    if not books:
        return []

    read = [b for b in books if b.is_read]
    unread = [b for b in books if not b.is_read]
    if not unread:
        return []

    # Авторы с высокой оценкой (4–5)
    liked_authors = {b.author_id for b in read if (b.rating or 0) >= 4}
    # Теги прочитанного/избранного → вес
    tag_weight: dict[str, int] = defaultdict(int)
    for b in read:
        for t in b.tags:
            tag_weight[t.name] += 2
    for b in books:
        if b.is_favorite:
            for t in b.tags:
                tag_weight[t.name] += 1

    # Циклы, которые пользователь читает: серия → макс. прочитанный порядок
    series_read_max: dict[int, float] = {}
    for b in read:
        if b.series_id is not None:
            order = b.series_order or 0
            series_read_max[b.series_id] = max(series_read_max.get(b.series_id, 0), order)

    scored = []
    for b in unread:
        score = 0
        reasons = []

        # Следующая книга в читаемом цикле
        if b.series_id in series_read_max:
            order = b.series_order or 0
            if order >= series_read_max[b.series_id]:
                score += 6
                sname = b.series.name if b.series else "цикл"
                reasons.append(f"Продолжение цикла «{sname}»")

        # Любимый автор
        if b.author_id in liked_authors:
            score += 4
            aname = b.author.name if b.author else "автор"
            reasons.append(f"Вы высоко оценили книги: {aname}")

        # Совпадение тегов
        tscore = sum(tag_weight.get(t.name, 0) for t in b.tags)
        if tscore:
            score += min(tscore, 5)
            shared = [t.name for t in b.tags if t.name in tag_weight][:3]
            if shared:
                reasons.append("По темам: " + ", ".join(shared))

        # Намерения пользователя
        if b.in_reading_list:
            score += 2
            reasons.append("В списке «Хочу прочитать»")
        if b.is_favorite:
            score += 1

        scored.append((score, b, reasons[0] if reasons else "Из вашей библиотеки"))

    return scored


def recommend(db: Session, user_id: int, limit: int = 6) -> list[dict]:
    scored = [c for c in _score_candidates(db, user_id) if c[0] > 0]
    scored.sort(key=lambda x: (x[0], x[1].added_at or x[1].id), reverse=True)
    return [
        {"book": b, "reason": reason, "score": score}
        for score, b, reason in scored[:limit]
    ]


def pick_random(db: Session, user_id: int) -> Book | None:
    """Взвешенный случайный выбор книги «что почитать» — книги с бОльшим скорингом
    выпадают чаще, но любая непрочитанная книга имеет ненулевой шанс (пол score=1)."""
    scored = _score_candidates(db, user_id)
    if not scored:
        return None
    population = [b for _, b, _ in scored]
    weights = [max(score, 1) for score, _, _ in scored]
    return random.choices(population, weights=weights, k=1)[0]


def pick_roulette(db: Session, user_id: int, exclude: str = "") -> dict | None:
    """Рулетка «что почитать»: случайный НЕпрочитанный цикл (есть хоть один
    непрочитанный том) или непрочитанная книга вне циклов. Каждый цикл — один
    «билет», независимо от числа томов. exclude — ключ прошлого выпадения
    («series:12» / «book:34»), чтобы «Ещё раз» не повторял то же самое.
    Возвращает dict для модального окна или None, если выбирать не из чего."""
    books = (
        db.query(Book)
        .filter(Book.user_id == user_id, Book.deleted_at.is_(None))
        .all()
    )
    series: dict[int, list] = defaultdict(list)
    standalone = []
    for b in books:
        if b.series_id:
            series[b.series_id].append(b)
        elif not b.is_read:
            standalone.append(b)

    pool = []
    for sid, vols in series.items():
        if any(not v.is_read for v in vols):
            pool.append(("series", sid, vols))
    for b in standalone:
        pool.append(("book", b.id, b))
    if not pool:
        return None
    if len(pool) > 1 and exclude:
        pool = [p for p in pool if f"{p[0]}:{p[1]}" != exclude] or pool

    kind, key, payload = random.choice(pool)
    if kind == "book":
        b = payload
        return {
            "kind": "book", "key": f"book:{b.id}", "title": b.title,
            "author": b.author.name if b.author else "",
            "cover_url": f"/books/{b.id}/cover" if b.cover_path else None,
            "url": f"/books/{b.id}",
            "read_url": f"/books/{b.id}/read" if b.file_format in ("epub", "fb2", "pdf") else None,
            "subtitle": "Книга вне циклов",
        }

    vols = sorted(payload, key=lambda v: (v.series_order is None, v.series_order or 0, v.title or ""))
    nxt = next((v for v in vols if not v.is_read), vols[0])
    cover_book = next((v for v in [nxt] + vols if v.cover_path), None)
    read_cnt = sum(1 for v in vols if v.is_read)
    authors = sorted({v.author.name for v in vols if v.author})
    order = nxt.series_order
    order_txt = (str(int(order)) if order is not None and float(order).is_integer() else str(order)) if order is not None else ""
    n = len(vols)
    word = "том" if n % 10 == 1 and n % 100 != 11 else ("тома" if n % 10 in (2, 3, 4) and n % 100 not in (12, 13, 14) else "томов")
    return {
        "kind": "series", "key": f"series:{key}",
        "title": vols[0].series.name if vols[0].series else nxt.title,
        "author": ", ".join(authors) if len(authors) <= 2 else "разные авторы",
        "cover_url": f"/books/{cover_book.id}/cover" if cover_book else None,
        "url": f"/series/{key}",
        "subtitle": f"Цикл · {n} {word} · прочитано {read_cnt}",
        "next_title": nxt.title, "next_order": order_txt,
        "read_url": f"/books/{nxt.id}/read" if nxt.file_format in ("epub", "fb2", "pdf") else None,
        "next_url": f"/books/{nxt.id}",
    }
