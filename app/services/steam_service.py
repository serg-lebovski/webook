"""Интеграция со Steam: данные об игре (Store API, без ключа) и время игры (Web API, с ключом)."""
from __future__ import annotations

import re

import httpx

_TIMEOUT = 15
_UA = {"User-Agent": "Mozilla/5.0 (WeBook)"}


def parse_appid(url_or_id: str) -> int | None:
    """AppID из ссылки store.steampowered.com/app/{id}/... или из числа."""
    s = (url_or_id or "").strip()
    if s.isdigit():
        return int(s)
    m = re.search(r"/app/(\d+)", s)
    return int(m.group(1)) if m else None


def fetch_appdetails(appid: int, lang: str = "russian") -> dict | None:
    """Данные об игре из публичного Store API. Возвращает нормализованный dict или None."""
    url = "https://store.steampowered.com/api/appdetails"
    params = {"appids": str(appid), "l": lang, "cc": "ru"}
    try:
        with httpx.Client(timeout=_TIMEOUT, headers=_UA) as c:
            r = c.get(url, params=params)
            r.raise_for_status()
            payload = r.json()
    except Exception:
        return None
    entry = (payload or {}).get(str(appid)) or {}
    if not entry.get("success"):
        return None
    d = entry.get("data") or {}
    if d.get("type") not in (None, "game", "dlc", "demo"):
        # всё равно вернём, но обычно нужна именно игра
        pass

    desc = d.get("short_description") or ""
    # detailed_description — HTML; берём short для чистого текста
    genres = ", ".join(g.get("description", "") for g in (d.get("genres") or []))
    year = None
    rd = (d.get("release_date") or {}).get("date") or ""
    ym = re.search(r"(\d{4})", rd)
    if ym:
        year = int(ym.group(1))
    shots = [s.get("path_full") for s in (d.get("screenshots") or []) if s.get("path_full")][:8]
    meta = (d.get("metacritic") or {}).get("score")
    return {
        "appid": appid,
        "title": d.get("name") or "",
        "description": desc,
        "cover_url": d.get("header_image") or "",
        "screenshots": shots,
        "genres": genres,
        "release_year": year,
        "metacritic": meta,
        "steam_url": f"https://store.steampowered.com/app/{appid}/",
    }


def fetch_cover_bytes(cover_url: str) -> bytes | None:
    if not cover_url:
        return None
    try:
        with httpx.Client(timeout=_TIMEOUT, headers=_UA) as c:
            r = c.get(cover_url)
            if r.status_code == 200 and r.headers.get("content-type", "").startswith("image/"):
                return r.content
    except Exception:
        pass
    return None


# ── Время игры (нужен Steam Web API key + SteamID64) ───────────────────────

def resolve_steamid(profile_url_or_id: str, api_key: str) -> str | None:
    """SteamID64 из ссылки на профиль (/profiles/<id64> или /id/<vanity>) или из числа."""
    s = (profile_url_or_id or "").strip()
    if s.isdigit() and len(s) >= 17:
        return s
    m = re.search(r"/profiles/(\d+)", s)
    if m:
        return m.group(1)
    m = re.search(r"/id/([^/]+)", s)
    vanity = m.group(1) if m else (s if s and "/" not in s else None)
    if not vanity or not api_key:
        return None
    try:
        with httpx.Client(timeout=_TIMEOUT, headers=_UA) as c:
            r = c.get("https://api.steampowered.com/ISteamUser/ResolveVanityURL/v1/",
                      params={"key": api_key, "vanityurl": vanity})
            j = r.json().get("response") or {}
            if j.get("success") == 1:
                return j.get("steamid")
    except Exception:
        pass
    return None


def owned_playtimes(api_key: str, steamid: str) -> dict[int, int]:
    """{appid: playtime_forever_minutes} по всем играм профиля (профиль должен быть открыт)."""
    if not api_key or not steamid:
        return {}
    try:
        with httpx.Client(timeout=_TIMEOUT, headers=_UA) as c:
            r = c.get("https://api.steampowered.com/IPlayerService/GetOwnedGames/v1/",
                      params={"key": api_key, "steamid": steamid,
                              "include_appinfo": "0", "include_played_free_games": "1",
                              "format": "json"})
            games = (r.json().get("response") or {}).get("games") or []
            return {g["appid"]: g.get("playtime_forever", 0) for g in games}
    except Exception:
        return {}
