"""Игры: ручное добавление или импорт из Steam, оценки, статусы, тир-лист."""
from datetime import datetime

from sqlalchemy import Column, Integer, String, Float, Boolean, Text, DateTime, ForeignKey, UniqueConstraint
from sqlalchemy.orm import relationship

from app.database import Base

# Статусы прохождения
GAME_STATUSES = [
    ("want", "Хочу пройти"),
    ("playing", "Прохожу"),
    ("completed", "Пройдено"),
    ("dropped", "Брошено"),
]
STATUS_LABELS = dict(GAME_STATUSES)

# Платформы по поколениям (для выпадающего списка)
PLATFORMS = [
    "PC", "PC (Steam)", "PC (Epic)", "PC (GOG)",
    "PlayStation 1", "PlayStation 2", "PlayStation 3", "PlayStation 4", "PlayStation 5",
    "PSP", "PS Vita",
    "Xbox", "Xbox 360", "Xbox One", "Xbox Series X/S",
    "Nintendo NES", "Nintendo SNES", "Nintendo 64", "GameCube",
    "Nintendo Wii", "Nintendo Wii U", "Nintendo Switch",
    "Game Boy", "Game Boy Advance", "Nintendo DS", "Nintendo 3DS",
    "Sega Mega Drive", "Sega Dreamcast",
    "Android", "iOS", "Другое",
]


class Game(Base):
    __tablename__ = "games"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    title = Column(String, nullable=False, index=True)
    platform = Column(String, default="")
    status = Column(String, default="want")          # want|playing|completed|dropped
    rating = Column(Integer, nullable=True)           # 1..10
    comment = Column(Text, default="")
    description = Column(Text, default="")
    cover_path = Column(String, nullable=True)        # под COVERS_DIR
    screenshots = Column(Text, default="")            # URL-ы (Steam CDN), по строке
    genres = Column(String, default="")
    release_year = Column(Integer, nullable=True)
    metacritic = Column(Integer, nullable=True)
    steam_appid = Column(Integer, nullable=True)
    steam_url = Column(String, default="")
    hltb_main = Column(Float, nullable=True)          # среднее прохождение, часы
    hltb_completionist = Column(Float, nullable=True)
    playtime_minutes = Column(Integer, nullable=True)  # из Steam-профиля
    is_favorite = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    completed_at = Column(DateTime, nullable=True)
    deleted_at = Column(DateTime, nullable=True)

    user = relationship("User")

    @property
    def screenshot_list(self) -> list[str]:
        return [s for s in (self.screenshots or "").splitlines() if s.strip()]

    @property
    def playtime_hours(self):
        return round(self.playtime_minutes / 60, 1) if self.playtime_minutes else None


class GameTier(Base):
    __tablename__ = "game_tiers"
    __table_args__ = (UniqueConstraint("user_id", "game_id", name="uq_game_tier"),)

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    game_id = Column(Integer, ForeignKey("games.id", ondelete="CASCADE"), nullable=False, index=True)
    tier = Column(String, default="C")  # S|A|B|C|D
