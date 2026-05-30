from sqlalchemy import Column, String
from app.database import Base


class SiteSettings(Base):
    __tablename__ = "site_settings"

    key = Column(String, primary_key=True)
    value = Column(String, nullable=False)
