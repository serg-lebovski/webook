"""In-memory реестр комнат синхронного чтения (лидер ведёт позицию, follower следует).

Комнаты живут в памяти процесса (как _jobs/_plans в import_jobs.py) — не переживают
рестарт, что нормально для короткой сессии совместного чтения. TTL + лимит на
случай забытых комнат.
"""
import random
import string
from datetime import datetime, timedelta

from fastapi import WebSocket

_rooms: dict[str, dict] = {}
_ROOM_TTL = timedelta(hours=6)
_MAX_ROOMS = 200


def _gen_code() -> str:
    alphabet = string.ascii_uppercase + string.digits
    while True:
        code = "".join(random.choices(alphabet, k=5))
        if code not in _rooms:
            return code


def _prune():
    now = datetime.utcnow()
    stale = [c for c, r in _rooms.items() if now - r["created_at"] > _ROOM_TTL]
    for c in stale:
        _rooms.pop(c, None)
    if len(_rooms) > _MAX_ROOMS:
        oldest = sorted(_rooms.items(), key=lambda kv: kv[1]["created_at"])[: len(_rooms) - _MAX_ROOMS]
        for c, _ in oldest:
            _rooms.pop(c, None)


def create_room(book_id: int, owner_user_id: int) -> str:
    _prune()
    code = _gen_code()
    _rooms[code] = {
        "book_id": book_id,
        "owner_user_id": owner_user_id,
        "created_at": datetime.utcnow(),
        "clients": [],
        "current_index": 0,
    }
    return code


def get_room(code: str) -> dict | None:
    return _rooms.get(code)


def add_client(code: str, ws: WebSocket) -> bool:
    room = _rooms.get(code)
    if not room:
        return False
    room["clients"].append(ws)
    return True


def remove_client(code: str, ws: WebSocket):
    room = _rooms.get(code)
    if not room:
        return
    if ws in room["clients"]:
        room["clients"].remove(ws)
    if not room["clients"]:
        _rooms.pop(code, None)


async def broadcast(code: str, index: int, sender: WebSocket):
    room = _rooms.get(code)
    if not room:
        return
    room["current_index"] = index
    for c in list(room["clients"]):
        if c is sender:
            continue
        try:
            await c.send_json({"index": index})
        except Exception:
            pass
