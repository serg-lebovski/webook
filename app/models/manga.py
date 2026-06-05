"""Манга: тайтл из глав, каждая глава — набор страниц-изображений."""
from datetime import datetime

from sqlalchemy import Column, Integer, String, Text, DateTime, ForeignKey, Boolean
from sqlalchemy.orm import relationship

from app.database import Base


class Manga(Base):
    __tablename__ = "manga"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    title = Column(String, nullable=False, index=True)
    author = Column(String, default="")
    description = Column(Text, default="")
    folder = Column(String, nullable=False)          # uuid-каталог под MANGA_DIR
    cover_path = Column(String, nullable=True)        # под COVERS_DIR
    is_favorite = Column(Boolean, default=False)
    # состояние чтения
    current_chapter_id = Column(Integer, nullable=True)
    current_page = Column(Integer, default=0)
    last_read_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    deleted_at = Column(DateTime, nullable=True)      # корзина

    chapters = relationship("MangaChapter", back_populates="manga",
                            cascade="all, delete-orphan", order_by="MangaChapter.order")

    @property
    def chapter_count(self) -> int:
        return len(self.chapters)

    @property
    def page_total(self) -> int:
        return sum(c.page_count or 0 for c in self.chapters)


class MangaChapter(Base):
    __tablename__ = "manga_chapters"

    id = Column(Integer, primary_key=True, index=True)
    manga_id = Column(Integer, ForeignKey("manga.id", ondelete="CASCADE"), nullable=False, index=True)
    title = Column(String, default="")
    order = Column(Integer, default=0)
    folder = Column(String, nullable=False)          # uuid-подкаталог под манги
    page_count = Column(Integer, default=0)
    created_at = Column(DateTime, default=datetime.utcnow)

    manga = relationship("Manga", back_populates="chapters")

    @property
    def label(self) -> str:
        return self.title or f"Глава {self.order}"
