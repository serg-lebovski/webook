"""Рекомендации «что почитать дальше» из собственной библиотеки пользователя.

Сигналы (по убыванию веса):
  • следующая книга в цикле, который пользователь уже читает;
  • непрочитанная книга автора, чьи книги оценены на 4–5;
  • совпадение тегов с прочитанным/избранным;
  • присутствие в списке «Хочу прочитать» / избранном.
Чисто по локальным данным, без внешних сервисов.
"""
from collections import defaultdict

from sqlalchemy.orm import Session

from app.models.book import Book


def recommend(db: Session, user_id: int, limit: int = 6) -> list[dict]:
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

        if score > 0:
            scored.append((score, b, reasons[0] if reasons else "Из вашей библиотеки"))

    scored.sort(key=lambda x: (x[0], x[1].added_at or x[1].id), reverse=True)
    return [
        {"book": b, "reason": reason, "score": score}
        for score, b, reason in scored[:limit]
    ]
