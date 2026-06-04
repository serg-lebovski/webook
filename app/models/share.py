import uuid
from datetime import datetime, timedelta
from sqlalchemy import Column, Integer, String, DateTime, ForeignKey, Boolean
from sqlalchemy.orm import relationship
from app.database import Base


class Share(Base):
    __tablename__ = "shares"

    id = Column(Integer, primary_key=True)
    token = Column(String, unique=True, index=True, default=lambda: uuid.uuid4().hex)
    owner_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    resource_type = Column(String, nullable=False)  # 'book' | 'link'
    resource_id = Column(Integer, nullable=False)
    is_public = Column(Boolean, default=True, nullable=False)
    shared_with_user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    expires_at = Column(DateTime, nullable=False,
                        default=lambda: datetime.utcnow() + timedelta(days=7))
    created_at = Column(DateTime, default=datetime.utcnow)
    # Только для публичных ссылок на файлы/папки:
    password_hash = Column(String, nullable=True)      # необязательная защита паролем
    max_downloads = Column(Integer, nullable=True)     # лимит скачиваний (NULL = без лимита)
    download_count = Column(Integer, default=0, nullable=False)

    owner = relationship("User", foreign_keys=[owner_id])
    shared_with = relationship("User", foreign_keys=[shared_with_user_id])

    @property
    def is_expired(self):
        return datetime.utcnow() > self.expires_at

    @property
    def limit_reached(self):
        return self.max_downloads is not None and (self.download_count or 0) >= self.max_downloads
