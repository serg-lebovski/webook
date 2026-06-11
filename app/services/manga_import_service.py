"""Импорт манги/манхвы по ссылке: скачивание изображений со страницы как главы.

Обобщённый загрузчик — берёт все картинки со страницы (включая ленивую загрузку
через data-src / srcset / <noscript>), скачивает их с правильным Referer и возвращает
естественно отсортированный список (имя, байты). Никаких обходов защит конкретных
сайтов — работает с обычными страницами-галереями.
"""
from __future__ import annotations

import re
from pathlib import PurePosixPath
from urllib.parse import urljoin, urlparse

import httpx
from lxml import html as lhtml

from app.services.manga_service import _natural_key

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)
_PAGE_TIMEOUT = 25
_IMG_TIMEOUT = 40
_MAX_IMAGES = 600
_MIN_BYTES = 8 * 1024          # отсекаем иконки/логотипы интерфейса
_MAX_IMG_BYTES = 30 * 1024 * 1024
_MAX_TOTAL_BYTES = 1024 * 1024 * 1024
_LAZY_ATTRS = ("data-src", "data-original", "data-lazy-src", "data-cfsrc",
               "data-url", "data-image", "src")
_IMG_EXT = (".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp", ".avif")


def _largest_from_srcset(srcset: str) -> str | None:
    """Из srcset берём кандидат с наибольшим дескриптором ширины."""
    best, best_w = None, -1
    for part in srcset.split(","):
        bits = part.strip().split()
        if not bits:
            continue
        url = bits[0]
        w = 0
        if len(bits) > 1 and bits[1].endswith("w"):
            try:
                w = int(bits[1][:-1])
            except ValueError:
                w = 0
        if w >= best_w:
            best, best_w = url, w
    return best


def extract_image_urls(page_html: str, base_url: str) -> list[str]:
    """Собрать URL изображений со страницы (с учётом ленивой загрузки)."""
    try:
        doc = lhtml.fromstring(page_html.encode("utf-8"), parser=lhtml.HTMLParser(encoding="utf-8"))
    except Exception:
        return []

    # <noscript> часто содержит настоящие <img> для lazy-load сайтов
    fragments = [doc]
    for ns in doc.xpath("//noscript"):
        try:
            fragments.append(lhtml.fragment_fromstring(ns.text_content(), create_parent="div"))
        except Exception:
            pass

    urls: list[str] = []
    seen: set[str] = set()

    def add(raw: str | None):
        if not raw:
            return
        raw = raw.strip()
        if not raw or raw.startswith("data:"):
            return
        absu = urljoin(base_url, raw)
        if absu not in seen:
            seen.add(absu)
            urls.append(absu)

    for frag in fragments:
        for img in frag.xpath(".//img"):
            srcset = img.get("srcset") or img.get("data-srcset")
            if srcset:
                add(_largest_from_srcset(srcset))
            for attr in _LAZY_ATTRS:
                if img.get(attr):
                    add(img.get(attr))
                    break
    return urls


def _looks_like_image(url: str, content_type: str) -> bool:
    if content_type.lower().startswith("image/"):
        return True
    ext = PurePosixPath(urlparse(url).path).suffix.lower()
    return ext in _IMG_EXT


def _name_for(idx: int, url: str, content_type: str) -> str:
    ext = PurePosixPath(urlparse(url).path).suffix.lower()
    if ext not in _IMG_EXT:
        ext = {
            "image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp",
            "image/gif": ".gif", "image/avif": ".avif", "image/bmp": ".bmp",
        }.get(content_type.lower().split(";")[0].strip(), ".jpg")
    return f"{idx:04d}{ext}"


def fetch_page_images(url: str) -> tuple[list[tuple[str, bytes]], str]:
    """Скачать все изображения со страницы.

    Возвращает (список (имя, байты), заголовок_страницы). Бросает ValueError с
    понятным сообщением при сетевых ошибках.
    """
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    headers = {"User-Agent": _UA, "Accept-Language": "ru,en;q=0.9"}

    try:
        with httpx.Client(follow_redirects=True, timeout=_PAGE_TIMEOUT, headers=headers) as client:
            resp = client.get(url)
            resp.raise_for_status()
            page_html = resp.text
            final_url = str(resp.url)

            img_urls = extract_image_urls(page_html, final_url)[:_MAX_IMAGES]
            if not img_urls:
                raise ValueError("На странице не найдено изображений.")

            title = ""
            m = re.search(r"<title[^>]*>([^<]+)</title>", page_html, re.I)
            if m:
                title = m.group(1).strip()

            images: list[tuple[str, bytes]] = []
            total = 0
            ref_headers = dict(headers)
            ref_headers["Referer"] = final_url
            for i, iu in enumerate(img_urls, start=1):
                try:
                    r = client.get(iu, headers=ref_headers, timeout=_IMG_TIMEOUT)
                    if r.status_code != 200:
                        continue
                    ctype = r.headers.get("content-type", "")
                    if not _looks_like_image(iu, ctype):
                        continue
                    data = r.content
                    if len(data) < _MIN_BYTES or len(data) > _MAX_IMG_BYTES:
                        continue
                    total += len(data)
                    if total > _MAX_TOTAL_BYTES:
                        break
                    images.append((_name_for(len(images) + 1, iu, ctype), data))
                except Exception:
                    continue
    except ValueError:
        raise
    except httpx.HTTPStatusError as e:
        raise ValueError(f"Страница недоступна (HTTP {e.response.status_code}).")
    except Exception as e:
        raise ValueError(f"Не удалось загрузить страницу: {e}")

    # сохраняем порядок появления на странице; если имена «цифровые» — натуральная сортировка
    if images and all(re.match(r"^\d+", n) for n, _ in images):
        images.sort(key=lambda p: _natural_key(p[0]))
    if not images:
        raise ValueError("Изображения найдены, но ни одно не удалось скачать "
                         "(возможна защита от хотлинка или загрузка через JavaScript).")
    return images, title
