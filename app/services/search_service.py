"""Полнотекстовый поиск по содержимому статей.

На PostgreSQL используется индексируемый `links.content_tsv` (tsvector, GIN-индекс,
русская конфигурация). Содержимое статьи лежит в файле (`./links/{id}.txt`), поэтому
tsvector пересобирается при каждом сохранении контента (`reindex_link`).
На SQLite (локальная разработка) FTS недоступен — поиск по тексту делается перебором
файлов в Python (см. `search.py`), а функции ниже становятся no-op.
"""
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.models.link import Link


def is_postgres(db: Session) -> bool:
    try:
        return db.get_bind().dialect.name == "postgresql"
    except Exception:
        return False


def reindex_link(db: Session, link: Link) -> None:
    """Пересобрать tsvector статьи из заголовка + описания + текста (PostgreSQL).

    Вызывающий код отвечает за commit. Ошибки проглатываются — индексация не должна
    ломать сохранение статьи.
    """
    if not is_postgres(db):
        return
    try:
        content = link.content or ""
        blob = " ".join(p for p in (link.title or "", link.description or "", content) if p)
        db.execute(
            text("UPDATE links SET content_tsv = to_tsvector('russian', :blob) WHERE id = :id"),
            {"blob": blob, "id": link.id},
        )
    except Exception:
        pass


def search_link_ids(db: Session, user_id: int, q: str) -> set[int]:
    """ID статей пользователя, чей текст соответствует запросу (PostgreSQL FTS)."""
    if not is_postgres(db) or not q.strip():
        return set()
    try:
        rows = db.execute(
            text(
                "SELECT id FROM links "
                "WHERE user_id = :uid AND deleted_at IS NULL "
                "AND content_tsv @@ plainto_tsquery('russian', :q)"
            ),
            {"uid": user_id, "q": q},
        ).all()
        return {r[0] for r in rows}
    except Exception:
        return set()
