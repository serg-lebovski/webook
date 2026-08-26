"""Коллекции/подборки: именованные списки из любого контента."""
from datetime import datetime

from sqlalchemy import Column, Integer, String, Text, DateTime, Boolean, ForeignKey
from sqlalchemy.orm import relationship

from app.database import Base


class Collection(Base):
    __tablename__ = "collections"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    name = Column(String, nullable=False)
    description = Column(Text, default="")
    created_at = Column(DateTime, default=datetime.utcnow)
    is_smart = Column(Boolean, nullable=False, default=False)
    rule_json = Column(Text, nullable=True)

    items = relationship("CollectionItem", back_populates="collection",
                         cascade="all, delete-orphan", order_by="CollectionItem.id")


class CollectionItem(Base):
    __tablename__ = "collection_items"

    id = Column(Integer, primary_key=True, index=True)
    collection_id = Column(Integer, ForeignKey("collections.id", ondelete="CASCADE"),
                           nullable=False, index=True)
    resource_type = Column(String, nullable=False)   # book | audiobook | manga | link
    resource_id = Column(Integer, nullable=False)
    added_at = Column(DateTime, default=datetime.utcnow)

    collection = relationship("Collection", back_populates="items")
