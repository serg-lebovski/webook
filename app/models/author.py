from sqlalchemy import Column, Integer, String
from sqlalchemy.orm import relationship
from app.database import Base


class Author(Base):
    __tablename__ = "authors"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False, index=True)
    bio = Column(String, default="")
    photo_path = Column(String, nullable=True)

    series = relationship("Series", back_populates="author")
    books = relationship("Book", back_populates="author")
