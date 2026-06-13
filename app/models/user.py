from datetime import datetime
from sqlalchemy import Column, Integer, String, Boolean, DateTime
from app.database import Base


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    username = Column(String, unique=True, nullable=False, index=True)
    password_hash = Column(String, nullable=False)
    is_admin = Column(Boolean, default=False)
    reading_goal = Column(Integer, default=0)  # годовая цель по книгам; 0 = не задана
    steam_profile_url = Column(String, default="")  # ссылка/ID профиля Steam для плейтайма
    steam_id = Column(String, default="")            # SteamID64 (резолвится из профиля)
    created_at = Column(DateTime, default=datetime.utcnow)
