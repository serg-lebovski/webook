"""Граф связей библиотеки: книги-узлы, рёбра — общий автор/серия/тег.

Только по данным пользователя, без внешних сервисов. Лимит узлов, чтобы
force-directed layout на клиенте оставался читаемым и быстрым.
"""
from collections import defaultdict

from sqlalchemy.orm import Session

from app.models.book import Book

MAX_NODES = 150


def build_graph(db: Session, user_id: int) -> dict:
    books = (
        db.query(Book)
        .filter(Book.user_id == user_id, Book.deleted_at.is_(None))
        .order_by(Book.added_at.desc())
        .limit(MAX_NODES)
        .all()
    )
    if not books:
        return {"nodes": [], "edges": []}

    nodes = [{
        "id": b.id,
        "title": b.title,
        "author": b.author.name if b.author else "",
        "cover_url": f"/books/{b.id}/cover" if b.cover_path else None,
        "url": f"/books/{b.id}",
    } for b in books]

    by_author: dict[int, list[int]] = defaultdict(list)
    by_series: dict[int, list[int]] = defaultdict(list)
    by_tag: dict[str, list[int]] = defaultdict(list)
    for b in books:
        if b.author_id:
            by_author[b.author_id].append(b.id)
        if b.series_id:
            by_series[b.series_id].append(b.id)
        for t in b.tags:
            by_tag[t.name].append(b.id)

    edge_weight: dict[tuple[int, int], dict] = {}

    def add_edges(groups: dict, kind: str, weight: int):
        for ids in groups.values():
            if len(ids) < 2:
                continue
            for i in range(len(ids)):
                for j in range(i + 1, len(ids)):
                    a, b_ = sorted((ids[i], ids[j]))
                    key = (a, b_)
                    e = edge_weight.setdefault(key, {"source": a, "target": b_, "weight": 0, "kinds": set()})
                    e["weight"] += weight
                    e["kinds"].add(kind)

    add_edges(by_author, "author", 3)
    add_edges(by_series, "series", 4)
    add_edges(by_tag, "tag", 1)

    edges = [
        {"source": e["source"], "target": e["target"], "weight": e["weight"], "kinds": sorted(e["kinds"])}
        for e in edge_weight.values()
    ]

    return {"nodes": nodes, "edges": edges}
