"""Создание и привязка тегов к книгам и статьям."""
from typing import List

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.tag import Tag, book_tags, link_tags


def parse_tag_names(raw: str) -> List[str]:
    """'история, наука ; история' -> ['история', 'наука'] (без дублей, до 30 шт.)."""
    seen: List[str] = []
    lowered = set()
    for part in (raw or "").replace(";", ",").split(","):
        name = part.strip()
        if name and name.lower() not in lowered:
            seen.append(name)
            lowered.add(name.lower())
    return seen[:30]


def get_or_create_tags(names: List[str], user_id: int, db: Session) -> List[Tag]:
    result: List[Tag] = []
    for name in names:
        tag = (
            db.query(Tag)
            .filter(Tag.user_id == user_id, func.lower(Tag.name) == name.lower())
            .first()
        )
        if not tag:
            tag = Tag(name=name, user_id=user_id)
            db.add(tag)
            db.flush()
        result.append(tag)
    return result


def set_tags_from_string(obj, raw: str, user_id: int, db: Session) -> None:
    """Назначает теги объекту (Book или Link) из строки, разделённой запятыми."""
    names = parse_tag_names(raw)
    obj.tags = get_or_create_tags(names, user_id, db)


def tags_to_string(obj) -> str:
    return ", ".join(t.name for t in obj.tags)


# ── Управление тегами ────────────────────────────────────────────────────────

def tag_usage(db: Session, user_id: int) -> List[dict]:
    """Список тегов пользователя со счётчиками использования (книги/статьи)."""
    tags = (
        db.query(Tag)
        .filter(Tag.user_id == user_id)
        .order_by(func.lower(Tag.name))
        .all()
    )
    result = []
    for t in tags:
        bc = db.query(book_tags).filter(book_tags.c.tag_id == t.id).count()
        lc = db.query(link_tags).filter(link_tags.c.tag_id == t.id).count()
        result.append({"tag": t, "books": bc, "links": lc, "total": bc + lc})
    return result


def _owned_tag(db: Session, tag_id: int, user_id: int):
    return db.query(Tag).filter(Tag.id == tag_id, Tag.user_id == user_id).first()


def _reassign(db: Session, source: Tag, target: Tag) -> None:
    """Переносит связи source→target, избегая дублей (PK book/tag, link/tag)."""
    target_books = {
        r.book_id for r in db.query(book_tags.c.book_id).filter(book_tags.c.tag_id == target.id).all()
    }
    for r in db.query(book_tags.c.book_id).filter(book_tags.c.tag_id == source.id).all():
        cond = (book_tags.c.tag_id == source.id) & (book_tags.c.book_id == r.book_id)
        if r.book_id in target_books:
            db.execute(book_tags.delete().where(cond))
        else:
            db.execute(book_tags.update().where(cond).values(tag_id=target.id))

    target_links = {
        r.link_id for r in db.query(link_tags.c.link_id).filter(link_tags.c.tag_id == target.id).all()
    }
    for r in db.query(link_tags.c.link_id).filter(link_tags.c.tag_id == source.id).all():
        cond = (link_tags.c.tag_id == source.id) & (link_tags.c.link_id == r.link_id)
        if r.link_id in target_links:
            db.execute(link_tags.delete().where(cond))
        else:
            db.execute(link_tags.update().where(cond).values(tag_id=target.id))


def rename_tag(db: Session, tag_id: int, new_name: str, user_id: int) -> bool:
    tag = _owned_tag(db, tag_id, user_id)
    new_name = (new_name or "").strip()
    if not tag or not new_name:
        return False
    existing = (
        db.query(Tag)
        .filter(Tag.user_id == user_id, func.lower(Tag.name) == new_name.lower(), Tag.id != tag.id)
        .first()
    )
    if existing:
        # имя занято другим тегом → сливаем текущий в него
        _reassign(db, tag, existing)
        db.delete(tag)
    else:
        tag.name = new_name
    db.commit()
    return True


def merge_tags(db: Session, source_id: int, target_id: int, user_id: int) -> bool:
    if source_id == target_id:
        return False
    source = _owned_tag(db, source_id, user_id)
    target = _owned_tag(db, target_id, user_id)
    if not source or not target:
        return False
    _reassign(db, source, target)
    db.delete(source)
    db.commit()
    return True


def delete_tag(db: Session, tag_id: int, user_id: int) -> bool:
    tag = _owned_tag(db, tag_id, user_id)
    if not tag:
        return False
    db.delete(tag)  # связи book_tags/link_tags уходят по ondelete=CASCADE
    db.commit()
    return True
