"""Fetch URL metadata (title, description) and article content."""
from __future__ import annotations
from datetime import datetime
from typing import Optional
import re


HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0 Safari/537.36"
    ),
    "Accept-Language": "ru,en;q=0.9",
}
TIMEOUT = 10


def _extract_meta(html: str) -> dict:
    """Extract title and description from HTML using og:* and meta tags."""
    title = ""
    description = ""

    og_title = re.search(r'<meta[^>]+property=["\']og:title["\'][^>]+content=["\']([^"\']+)["\']', html, re.I)
    if not og_title:
        og_title = re.search(r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:title["\']', html, re.I)
    if og_title:
        title = og_title.group(1).strip()

    if not title:
        m = re.search(r'<title[^>]*>([^<]+)</title>', html, re.I)
        if m:
            title = m.group(1).strip()

    og_desc = re.search(r'<meta[^>]+property=["\']og:description["\'][^>]+content=["\']([^"\']+)["\']', html, re.I)
    if not og_desc:
        og_desc = re.search(r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:description["\']', html, re.I)
    if og_desc:
        description = og_desc.group(1).strip()

    if not description:
        m = re.search(r'<meta[^>]+name=["\']description["\'][^>]+content=["\']([^"\']+)["\']', html, re.I)
        if not m:
            m = re.search(r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+name=["\']description["\']', html, re.I)
        if m:
            description = m.group(1).strip()

    # Decode HTML entities
    for entity, char in [("&amp;", "&"), ("&quot;", '"'), ("&#39;", "'"), ("&lt;", "<"), ("&gt;", ">")]:
        title = title.replace(entity, char)
        description = description.replace(entity, char)

    return {"title": title, "description": description}


def _extract_content(html: str, url: str) -> Optional[str]:
    """Extract main article text using trafilatura."""
    try:
        import trafilatura
        text = trafilatura.extract(
            html,
            url=url,
            include_comments=False,
            include_tables=True,
            favor_recall=True,
            deduplicate=True,
        )
        return text
    except Exception:
        return None


def detect_video_embed(url: str) -> Optional[str]:
    """Return embed URL for YouTube/Vimeo URLs, None otherwise."""
    m = re.match(r'https?://(?:www\.)?youtube\.com/watch\?.*v=([a-zA-Z0-9_-]+)', url)
    if m:
        return f"https://www.youtube.com/embed/{m.group(1)}"
    m = re.match(r'https?://youtu\.be/([a-zA-Z0-9_-]+)', url)
    if m:
        return f"https://www.youtube.com/embed/{m.group(1)}"
    m = re.match(r'https?://(?:www\.)?vimeo\.com/(\d+)', url)
    if m:
        return f"https://player.vimeo.com/video/{m.group(1)}"
    return None


def fetch_meta_only(url: str) -> dict:
    """Fetch title/description without full content extraction. Returns {title, description, is_video, embed_url}."""
    result = {"title": "", "description": "", "is_video": False, "embed_url": None}
    embed = detect_video_embed(url)
    if embed:
        result["is_video"] = True
        result["embed_url"] = embed
        return result
    try:
        import httpx
        with httpx.Client(headers=HEADERS, timeout=5, follow_redirects=True) as client:
            resp = client.get(url)
            if resp.status_code < 400 and "html" in resp.headers.get("content-type", ""):
                meta = _extract_meta(resp.text)
                result["title"] = meta["title"]
                result["description"] = meta["description"]
    except Exception:
        pass
    return result


def fetch_link_data(url: str) -> dict:
    """Fetch URL and return {title, description, content}. Never raises."""
    result = {"title": "", "description": "", "content": None}
    try:
        import httpx
        with httpx.Client(headers=HEADERS, timeout=TIMEOUT, follow_redirects=True) as client:
            resp = client.get(url)
            if resp.status_code >= 400:
                return result
            ct = resp.headers.get("content-type", "")
            if "html" not in ct:
                return result
            html = resp.text
        meta = _extract_meta(html)
        result["title"] = meta["title"]
        result["description"] = meta["description"]
        result["content"] = _extract_content(html, url)
    except Exception:
        pass
    return result
