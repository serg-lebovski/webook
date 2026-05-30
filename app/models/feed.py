from datetime import datetime
from sqlalchemy import Column, Integer, String, DateTime, ForeignKey
from sqlalchemy.orm import relationship
from app.database import Base


class Feed(Base):
    """RSS/Atom-подписка: новые записи автоматически становятся ссылками (Link)."""
    __tablename__ = "feeds"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    url = Column(String, nullable=False)
    title = Column(String, default="")
    folder_id = Column(Integer, ForeignKey("link_folders.id"), nullable=True)
    etag = Column(String, nullable=True)
    last_modified = Column(String, nullable=True)
    last_checked = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    folder = relationship("LinkFolder")
