from sqlalchemy import Column, Integer, String, ForeignKey, UniqueConstraint
from sqlalchemy.orm import relationship
from app.database import Base


class SeriesTier(Base):
    """Ранг цикла в личном тир-листе пользователя (S/A/B/C/D)."""
    __tablename__ = "series_tiers"
    __table_args__ = (UniqueConstraint("user_id", "series_id", name="uq_series_tier_user_series"),)

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    series_id = Column(Integer, ForeignKey("series.id", ondelete="CASCADE"), nullable=False, index=True)
    tier = Column(String, nullable=False)  # один из TIERS

    series = relationship("Series")
