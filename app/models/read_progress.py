from datetime import datetime
from sqlalchemy import Column, Integer, String, Float, DateTime, ForeignKey, UniqueConstraint
from app.database import Base


class ReadProgress(Base):
    __tablename__ = "read_progress"
    __table_args__ = (UniqueConstraint("user_id", "book_id", name="uq_rp_user_book"),)

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    book_id = Column(Integer, ForeignKey("books.id"), nullable=False)
    progress = Column(String, nullable=True)
    percentage = Column(Float, nullable=True)
    updated_at = Column(DateTime, default=datetime.utcnow)
