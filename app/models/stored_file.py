"""Файловая шара: папки и загруженные файлы пользователя."""
from datetime import datetime
from pathlib import PurePosixPath

from sqlalchemy import Column, Integer, String, BigInteger, DateTime, ForeignKey
from sqlalchemy.orm import relationship

from app.database import Base

_IMAGE_EXT = {"jpg", "jpeg", "png", "gif", "webp", "bmp", "svg", "avif"}
_VIDEO_EXT = {"mp4", "webm", "ogv", "mov", "m4v", "mkv"}
_AUDIO_EXT = {"mp3", "m4a", "ogg", "oga", "opus", "wav", "flac", "aac"}
_PDF_EXT = {"pdf"}


class FileFolder(Base):
    __tablename__ = "file_folders"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    name = Column(String, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    files = relationship("StoredFile", back_populates="folder")


class StoredFile(Base):
    __tablename__ = "stored_files"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    folder_id = Column(Integer, ForeignKey("file_folders.id"), nullable=True, index=True)
    original_name = Column(String, nullable=False)   # человекочитаемое имя
    stored_name = Column(String, nullable=False)     # uuid-имя на диске
    size = Column(BigInteger, default=0)
    content_type = Column(String, default="application/octet-stream")
    created_at = Column(DateTime, default=datetime.utcnow)
    deleted_at = Column(DateTime, nullable=True)  # корзина (soft-delete)

    folder = relationship("FileFolder", back_populates="files")

    @property
    def ext(self) -> str:
        return PurePosixPath(self.original_name or "").suffix.lstrip(".").lower()

    @property
    def is_image(self) -> bool:
        return self.ext in _IMAGE_EXT

    @property
    def is_video(self) -> bool:
        return self.ext in _VIDEO_EXT

    @property
    def is_audio(self) -> bool:
        return self.ext in _AUDIO_EXT

    @property
    def is_pdf(self) -> bool:
        return self.ext in _PDF_EXT

    @property
    def previewable(self) -> bool:
        return self.is_image or self.is_video or self.is_audio or self.is_pdf

    @property
    def icon(self) -> str:
        if self.is_image:
            return "file-image"
        if self.is_video:
            return "file-play"
        if self.is_audio:
            return "file-music"
        if self.is_pdf:
            return "file-pdf"
        if self.ext in {"zip", "rar", "7z", "tar", "gz"}:
            return "file-zip"
        if self.ext in {"doc", "docx", "odt", "rtf"}:
            return "file-word"
        if self.ext in {"xls", "xlsx", "ods", "csv"}:
            return "file-spreadsheet"
        if self.ext in {"txt", "md", "log"}:
            return "file-text"
        return "file-earmark"

    @property
    def size_human(self) -> str:
        n = float(self.size or 0)
        for unit in ("Б", "КБ", "МБ", "ГБ"):
            if n < 1024 or unit == "ГБ":
                return f"{n:.0f} {unit}" if unit == "Б" else f"{n:.1f} {unit}"
            n /= 1024
        return f"{n:.1f} ГБ"
