import math
from datetime import datetime
from sqlalchemy import Column, Integer, String, Boolean, DateTime, ForeignKey, Text, Float
from sqlalchemy.orm import relationship
from app.database import Base


class LinkFolder(Base):
    __tablename__ = "link_folders"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    sort_order = Column(Integer, default=0)
    created_at = Column(DateTime, default=datetime.utcnow)

    user = relationship("User")
    links = relationship("Link", back_populates="folder", cascade="all, delete-orphan")


class Link(Base):
    __tablename__ = "links"

    id = Column(Integer, primary_key=True, index=True)
    title = Column(String, nullable=False)
    url = Column(String, nullable=False)
    description = Column(Text, default="")
    is_read = Column(Boolean, default=False)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    folder_id = Column(Integer, ForeignKey("link_folders.id"), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    read_at = Column(DateTime, nullable=True)
    content_fetched_at = Column(DateTime, nullable=True)
    word_count = Column(Integer, default=0)
    read_progress = Column(Float, default=0.0)  # 0..1, прокрутка статьи
    deleted_at = Column(DateTime, nullable=True)  # корзина (soft-delete)

    user = relationship("User")
    folder = relationship("LinkFolder", back_populates="links")
    tags = relationship("Tag", secondary="link_tags", lazy="selectin", order_by="Tag.name")

    @property
    def reading_minutes(self) -> int:
        """Оценка времени чтения (≈200 слов/мин)."""
        if not self.word_count:
            return 0
        return max(1, math.ceil(self.word_count / 200))

    @property
    def content(self):
        from app.config import LINKS_CONTENT_DIR
        path = LINKS_CONTENT_DIR / f"{self.id}.txt"
        if path.exists():
            return path.read_text(encoding="utf-8")
        return None

    @property
    def video_embed_url(self):
        from app.services.fetch_service import detect_video_embed
        return detect_video_embed(self.url) if self.url else None
