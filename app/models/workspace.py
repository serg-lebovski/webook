from datetime import datetime
from sqlalchemy import Column, Integer, String, Text, DateTime, ForeignKey
from sqlalchemy.orm import relationship
from app.database import Base


class Task(Base):
    __tablename__ = "tasks"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    title = Column(String, nullable=False)
    content = Column(Text, default="")
    status = Column(String, default="todo")   # todo | doing | done
    due_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

    images = relationship("TaskImage", back_populates="task",
                          cascade="all, delete-orphan", order_by="TaskImage.id")
    checklist = relationship("TaskChecklistItem", back_populates="task",
                             cascade="all, delete-orphan", order_by="TaskChecklistItem.id")

    @property
    def checklist_done(self) -> int:
        return sum(1 for i in self.checklist if i.done)


class TaskChecklistItem(Base):
    __tablename__ = "task_checklist"

    id = Column(Integer, primary_key=True, index=True)
    task_id = Column(Integer, ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False, index=True)
    text = Column(String, nullable=False)
    done = Column(Integer, default=0)  # 0/1 — чекбокс

    task = relationship("Task", back_populates="checklist")


class TaskImage(Base):
    __tablename__ = "task_images"

    id = Column(Integer, primary_key=True, index=True)
    task_id = Column(Integer, ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False, index=True)
    filename = Column(String, nullable=False)

    task = relationship("Task", back_populates="images")


class Note(Base):
    __tablename__ = "notes"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    title = Column(String, nullable=False, default="Без названия")
    content = Column(Text, default="")        # markdown
    remind_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)


class TimeInterval(Base):
    """Отрезок времени в тайм-менеджере. Непрерывная цепочка отрезков: «отметка»
    закрывает текущий и открывает новый; «стоп» закрывает без нового."""
    __tablename__ = "time_intervals"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    started_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    ended_at = Column(DateTime, nullable=True)        # NULL — отрезок идёт сейчас
    label = Column(Text, default="")                  # что делал
    kind = Column(String, default="work")             # work | rest
    target_seconds = Column(Integer, nullable=True)   # для обратного таймера (отдых)
    created_at = Column(DateTime, default=datetime.utcnow)

    @property
    def duration_seconds(self) -> float:
        end = self.ended_at or datetime.utcnow()
        return max(0.0, (end - self.started_at).total_seconds())
