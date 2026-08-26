from datetime import datetime
from sqlalchemy import Column, Integer, String, DateTime, ForeignKey
from app.database import Base


class Bookmark(Base):
    """Именованная закладка на позицию в книге (EPUB/FB2) или статье, в дополнение
    к единственной автосохраняемой позиции чтения (ReadProgress/Link.read_progress)."""
    __tablename__ = "bookmarks"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    resource_type = Column(String, nullable=False)   # 'book' | 'link'
    resource_id = Column(Integer, nullable=False, index=True)
    location = Column(String, nullable=False)         # CFI (epub), scrollTop (fb2) или char offset (статья)
    label = Column(String, default="")
    created_at = Column(DateTime, default=datetime.utcnow)
