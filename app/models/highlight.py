from datetime import datetime
from sqlalchemy import Column, Integer, String, Text, DateTime, ForeignKey
from app.database import Base


class Highlight(Base):
    """Цитата/заметка пользователя из книги (EPUB/FB2) или статьи."""
    __tablename__ = "highlights"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    resource_type = Column(String, nullable=False)   # 'book' | 'link'
    resource_id = Column(Integer, nullable=False, index=True)
    quote = Column(Text, nullable=False)
    note = Column(Text, default="")
    location = Column(String, nullable=True)          # CFI (epub) или "start-end" (html)
    color = Column(String, default="yellow")
    created_at = Column(DateTime, default=datetime.utcnow)
