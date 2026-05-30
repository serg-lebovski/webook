from datetime import datetime
from sqlalchemy import Column, Integer, String, DateTime, Boolean
from app.database import Base


class LoginAttempt(Base):
    """Журнал неудачных попыток входа для защиты от брутфорса."""
    __tablename__ = "login_attempts"

    id = Column(Integer, primary_key=True, index=True)
    ip = Column(String, nullable=False, index=True)
    success = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow, index=True)


class IpBan(Base):
    """Активные блокировки IP-адресов."""
    __tablename__ = "ip_bans"

    ip = Column(String, primary_key=True)
    until = Column(DateTime, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
