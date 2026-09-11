"""Коллаж из обложек библиотеки пользователя — заставка при запуске PWA.

Кэшируется на диске на сутки, чтобы не пересобирать картинку на каждый запрос.
"""
import random
from datetime import datetime, timedelta
from io import BytesIO

from sqlalchemy.orm import Session

from app.config import COVERS_DIR
from app.models.book import Book

CACHE_DIR = COVERS_DIR / "_splash_cache"
CACHE_TTL = timedelta(days=1)
GRID = 4  # 4x4 плиток
TILE = 90
SIZE = GRID * TILE


def _cache_path(user_id: int):
    return CACHE_DIR / f"{user_id}.png"


def generate_collage(db: Session, user_id: int) -> bytes | None:
    path = _cache_path(user_id)
    if path.exists():
        age = datetime.utcnow() - datetime.utcfromtimestamp(path.stat().st_mtime)
        if age < CACHE_TTL:
            return path.read_bytes()

    books = (
        db.query(Book)
        .filter(Book.user_id == user_id, Book.deleted_at.is_(None), Book.cover_path.isnot(None))
        .all()
    )
    if not books:
        return None

    from PIL import Image, ImageDraw

    random.shuffle(books)
    canvas = Image.new("RGB", (SIZE, SIZE), (20, 20, 30))
    slots = GRID * GRID
    placed = 0
    for b in books:
        if placed >= slots:
            break
        cover_path = COVERS_DIR / b.cover_path
        if not cover_path.exists():
            continue
        try:
            img = Image.open(cover_path).convert("RGB")
        except Exception:
            continue
        img = _cover_fit(img, TILE, TILE)
        x = (placed % GRID) * TILE
        y = (placed // GRID) * TILE
        canvas.paste(img, (x, y))
        placed += 1

    if placed == 0:
        return None

    # Затемнение снизу для читаемости лого/текста поверх коллажа
    overlay = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    for i in range(SIZE // 3):
        alpha = int(180 * (i / (SIZE // 3)))
        y = SIZE - i - 1
        draw.line([(0, y), (SIZE, y)], fill=(0, 0, 0, alpha))
    canvas = Image.alpha_composite(canvas.convert("RGBA"), overlay).convert("RGB")

    buf = BytesIO()
    canvas.save(buf, "PNG")
    data = buf.getvalue()

    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return data


def _cover_fit(img, w: int, h: int):
    from PIL import Image
    src_ratio = img.width / img.height
    dst_ratio = w / h
    if src_ratio > dst_ratio:
        new_h = h
        new_w = int(h * src_ratio)
    else:
        new_w = w
        new_h = int(w / src_ratio)
    img = img.resize((new_w, new_h), Image.LANCZOS)
    left = (new_w - w) // 2
    top = (new_h - h) // 2
    return img.crop((left, top, left + w, top + h))
