"""Среднее время прохождения с howlongtobeat.com — best-effort.

У HLTB нет официального API, внутренний эндпоинт периодически меняется. Поэтому
делаем аккуратную попытку и при неудаче возвращаем None — поле остаётся
редактируемым вручную.
"""
from __future__ import annotations

import re

import httpx

_TIMEOUT = 15
_BASE = "https://howlongtobeat.com"
_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Referer": _BASE + "/",
    "Origin": _BASE,
    "Content-Type": "application/json",
    "Accept": "*/*",
}


def _find_search_endpoint(client: httpx.Client) -> str | None:
    """Достаём актуальный путь поиска из JS-бандла (меняется со временем)."""
    try:
        html = client.get(_BASE + "/", headers={"User-Agent": _HEADERS["User-Agent"]}).text
        for m in re.finditer(r'/_next/static/chunks/[^"\']+\.js', html):
            js = client.get(_BASE + m.group(0), headers={"User-Agent": _HEADERS["User-Agent"]}).text
            mm = re.search(r'"/api/(seek|search)/?"\s*\.concat\("([^"]+)"\)', js)
            if mm:
                return f"/api/{mm.group(1)}/{mm.group(2)}"
            mm = re.search(r'/api/(seek|search)/[A-Za-z0-9]+', js)
            if mm:
                return mm.group(0)
    except Exception:
        pass
    return None


def search_hours(title: str) -> tuple[float | None, float | None]:
    """(main_story_hours, completionist_hours) или (None, None)."""
    title = (title or "").strip()
    if not title:
        return None, None
    payload = {
        "searchType": "games",
        "searchTerms": title.split(),
        "searchPage": 1, "size": 1,
        "searchOptions": {
            "games": {"userId": 0, "platform": "", "sortCategory": "popular",
                      "rangeCategory": "main", "rangeTime": {"min": None, "max": None},
                      "gameplay": {"perspective": "", "flow": "", "genre": ""},
                      "modifier": ""},
            "users": {"sortCategory": "postcount"},
            "filter": "", "sort": 0, "randomizer": 0,
        },
    }
    try:
        with httpx.Client(timeout=_TIMEOUT) as client:
            for path in [_find_search_endpoint(client), "/api/search", "/api/seek"]:
                if not path:
                    continue
                try:
                    r = client.post(_BASE + path, json=payload, headers=_HEADERS)
                    if r.status_code != 200:
                        continue
                    data = r.json()
                    items = data.get("data") or []
                    if not items:
                        continue
                    g = items[0]
                    main = g.get("comp_main") or 0
                    comp = g.get("comp_100") or 0
                    return (round(main / 3600, 1) if main else None,
                            round(comp / 3600, 1) if comp else None)
                except Exception:
                    continue
    except Exception:
        pass
    return None, None
