from datetime import datetime
from sqlalchemy import Column, Integer, String, Float, Boolean, ForeignKey, DateTime
from sqlalchemy.orm import relationship
from app.database import Base


class Audiobook(Base):
    """Аудиокнига: одна или несколько глав-файлов в общей папке."""
    __tablename__ = "audiobooks"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    title = Column(String, nullable=False, index=True)
    author = Column(String, default="")
    narrator = Column(String, default="")        # чтец
    description = Column(String, default="")
    folder = Column(String, nullable=False)        # имя папки в AUDIOBOOKS_DIR (uuid)
    cover_path = Column(String, nullable=True)     # имя файла обложки в COVERS_DIR

    # Статус прослушивания
    current_track_id = Column(Integer, nullable=True)   # на какой главе остановились
    position = Column(Float, default=0)                 # секунда внутри текущей главы
    is_finished = Column(Boolean, default=False)

    created_at = Column(DateTime, default=datetime.utcnow)
    last_played_at = Column(DateTime, nullable=True)

    user = relationship("User")
    tracks = relationship(
        "AudiobookTrack",
        back_populates="audiobook",
        cascade="all, delete-orphan",
        order_by="AudiobookTrack.order",
    )

    @property
    def total_duration(self) -> float:
        return sum((t.duration or 0) for t in self.tracks)


class AudiobookTrack(Base):
    """Отдельный аудиофайл (глава) внутри аудиокниги."""
    __tablename__ = "audiobook_tracks"

    id = Column(Integer, primary_key=True, index=True)
    audiobook_id = Column(Integer, ForeignKey("audiobooks.id", ondelete="CASCADE"), nullable=False, index=True)
    filename = Column(String, nullable=False)      # имя файла внутри папки аудиокниги
    title = Column(String, default="")             # отображаемое название главы
    order = Column(Integer, default=0)
    duration = Column(Float, nullable=True)        # секунды, если удалось определить
    file_format = Column(String, default="")
    file_size = Column(Integer, default=0)

    audiobook = relationship("Audiobook", back_populates="tracks")
