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
    created_at = Column(DateTime, default=datetime.utcnow)
    # Telegram-уведомления
    telegram_chat_id = Column(String, nullable=True)
    telegram_link_code = Column(String, nullable=True)
    tg_last_check = Column(DateTime, nullable=True)
