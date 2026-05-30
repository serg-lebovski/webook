from datetime import datetime
from sqlalchemy import Column, Integer, String, Float, Boolean, ForeignKey, DateTime
from sqlalchemy.orm import relationship
from app.database import Base


class Book(Base):
    __tablename__ = "books"

    id = Column(Integer, primary_key=True, index=True)
    title = Column(String, nullable=False, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True, index=True)
    author_id = Column(Integer, ForeignKey("authors.id"), nullable=False)
    series_id = Column(Integer, ForeignKey("series.id"), nullable=True)
    series_order = Column(Float, nullable=True)
    shelf_id = Column(Integer, ForeignKey("shelves.id"), nullable=False)
    description = Column(String, default="")
    cover_path = Column(String, nullable=True)
    file_path = Column(String, nullable=False)
    file_format = Column(String, nullable=False)
    file_size = Column(Integer, default=0)
    language = Column(String, default="")
    published_year = Column(Integer, nullable=True)
    added_at = Column(DateTime, default=datetime.utcnow)
    is_read = Column(Boolean, default=False)
    read_at = Column(DateTime, nullable=True)
    rating = Column(Integer, nullable=True)        # 1..5, NULL — без оценки
    is_favorite = Column(Boolean, default=False)

    user = relationship("User")
    author = relationship("Author", back_populates="books")
    series = relationship("Series", back_populates="books")
    shelf = relationship("Shelf", back_populates="books")
    tags = relationship("Tag", secondary="book_tags", lazy="selectin", order_by="Tag.name")
