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

_MAX_SUBPAGES = 250            # защита от бесконечного перелистывания
_MAX_CHAPTERS = 400            # предел числа глав при импорте серии
_READ_TEXT = re.compile(r"чита|read\s*now|read\s*manga|начать\s*чтен|start\s*read", re.I)
_READ_HREF = re.compile(r"(read|chapter|chap-|/chap/|glava|глава|vol)", re.I)
_NEXT_TEXT = re.compile(r"след(ующ)?|next|вперёд|вперед|дальше|»|›|→", re.I)
_CHAPTER_HREF = re.compile(r"(chapter|chap[-_/]|/ch\d|глав|glav|tom|том|vol(ume)?[-_/ ]?\d)", re.I)
_NUM_RE = re.compile(r"\d+(?:[.,]\d+)?")


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


def _meta_content(doc, *names) -> str:
    """Значение первого найденного <meta property/name=...>."""
    for n in names:
        for attr in ("property", "name", "itemprop"):
            vals = doc.xpath(f'//meta[@{attr}="{n}"]/@content')
            if vals and vals[0].strip():
                return vals[0].strip()
    return ""


def extract_meta(page_html: str, base_url: str) -> dict:
    """Заголовок, описание, обложка (og:image) и ссылка «Читать» со страницы серии."""
    try:
        doc = lhtml.fromstring(page_html.encode("utf-8"),
                               parser=lhtml.HTMLParser(encoding="utf-8"))
    except Exception:
        return {}

    title = _meta_content(doc, "og:title")
    if not title:
        t = doc.xpath("//title/text()")
        title = t[0].strip() if t else ""
    description = _meta_content(doc, "og:description", "description")
    cover = _meta_content(doc, "og:image", "twitter:image")
    cover = urljoin(base_url, cover) if cover else ""

    read_url = ""
    for a in doc.xpath("//a[@href]"):
        href = a.get("href") or ""
        if href.startswith(("#", "javascript:")):
            continue
        text = (a.text_content() or "").strip()
        cls = " ".join(a.classes)
        if _READ_TEXT.search(text) or _READ_TEXT.search(cls) or _READ_HREF.search(href):
            read_url = urljoin(base_url, href)
            break

    return {"title": title, "description": description, "cover": cover, "read_url": read_url}


def find_next_url(page_html: str, base_url: str, current_url: str) -> str | None:
    """Ссылка на следующую страницу постраничной читалки (rel=next или по тексту)."""
    try:
        doc = lhtml.fromstring(page_html.encode("utf-8"),
                               parser=lhtml.HTMLParser(encoding="utf-8"))
    except Exception:
        return None
    for sel in ('//link[@rel="next"]/@href', '//a[@rel="next"]/@href'):
        vals = doc.xpath(sel)
        if vals:
            nxt = urljoin(base_url, vals[0])
            if nxt != current_url:
                return nxt
    for a in doc.xpath("//a[@href]"):
        href = a.get("href") or ""
        if href.startswith(("#", "javascript:")):
            continue
        text = (a.text_content() or "").strip()
        cls = " ".join(a.classes)
        if (_NEXT_TEXT.search(text) or "next" in cls.lower()) and len(text) < 30:
            nxt = urljoin(base_url, href)
            if nxt != current_url:
                return nxt
    return None


def _download_images(client, img_urls, referer, start_idx, budget, on_page=None):
    """Скачать изображения. budget — изменяемый dict со счётчиком total байт.
    on_page(n) — колбэк с числом скачанных страниц (для прогресса)."""
    headers = {"User-Agent": _UA, "Accept-Language": "ru,en;q=0.9", "Referer": referer}
    out = []
    for iu in img_urls:
        try:
            r = client.get(iu, headers=headers, timeout=_IMG_TIMEOUT)
            if r.status_code != 200:
                continue
            ctype = r.headers.get("content-type", "")
            if not _looks_like_image(iu, ctype):
                continue
            data = r.content
            if len(data) < _MIN_BYTES or len(data) > _MAX_IMG_BYTES:
                continue
            budget["total"] += len(data)
            if budget["total"] > _MAX_TOTAL_BYTES:
                break
            out.append((_name_for(start_idx + len(out), iu, ctype), data))
            if on_page:
                on_page(start_idx - 1 + len(out))
        except Exception:
            continue
    return out


def make_client():
    return httpx.Client(follow_redirects=True, timeout=_PAGE_TIMEOUT,
                        headers={"User-Agent": _UA, "Accept-Language": "ru,en;q=0.9"})


def download_cover(client, cover_url, referer):
    if not cover_url:
        return None
    try:
        cr = client.get(cover_url, headers={"User-Agent": _UA, "Referer": referer})
        if cr.status_code == 200 and cr.headers.get("content-type", "").startswith("image/") \
                and len(cr.content) >= _MIN_BYTES:
            return cr.content
    except Exception:
        pass
    return None


def extract_chapters(series_html: str, base_url: str) -> list[dict]:
    """Главы серии: [{url, label}] — упорядочены по номеру (возр.). С подписями."""
    try:
        doc = lhtml.fromstring(series_html.encode("utf-8"),
                               parser=lhtml.HTMLParser(encoding="utf-8"))
    except Exception:
        return []
    base_path = urlparse(base_url).path.rstrip("/")
    seen: set[str] = set()
    items: list[dict] = []
    for a in doc.xpath("//a[@href]"):
        href = a.get("href") or ""
        if href.startswith(("#", "javascript:", "mailto:")):
            continue
        absu = urljoin(base_url, href).split("#")[0]
        path = urlparse(absu).path
        tail = path[len(base_path):] if base_path and path.startswith(base_path + "/") else ""
        text = " ".join((a.text_content() or "").split())[:80]

        tail_nums = _NUM_RE.findall(tail) if tail else []
        is_chapter = bool(tail and tail_nums) or (_CHAPTER_HREF.search(absu) and _NUM_RE.search(path))
        if not is_chapter:
            continue
        # кнопка «Читать» без номера в адресе — не глава
        if _READ_TEXT.search(text) and not tail_nums:
            continue
        if absu in seen or absu.rstrip("/") == base_url.rstrip("/"):
            continue
        seen.add(absu)

        all_nums = [float(n.replace(",", ".")) for n in _NUM_RE.findall(path)]
        items.append({"url": absu, "_nums": all_nums or [0.0], "_text": text})

    items.sort(key=lambda it: it["_nums"])
    out = []
    for i, it in enumerate(items, start=1):
        n = it["_nums"][-1]
        num_str = str(int(n)) if float(n).is_integer() else str(n)
        # подпись: «Глава N» по номеру из URL; если номера нет — текст ссылки/индекс
        label = f"Глава {num_str}" if it["_nums"] != [0.0] else (it["_text"] or f"Глава {i}")
        out.append({"url": it["url"], "label": label})
    return out[:_MAX_CHAPTERS]


def plan_import(url: str, series: bool) -> dict:
    """Разведка БЕЗ скачивания: метаданные + список глав (для серии) или
    список URL страниц (для одиночной главы). Для предпросмотра/редактирования."""
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    try:
        with make_client() as client:
            resp = client.get(url)
            resp.raise_for_status()
            page_html, final_url = resp.text, str(resp.url)
            meta = extract_meta(page_html, final_url)
            if series:
                chapters = extract_chapters(page_html, final_url)
                if not chapters and meta.get("read_url"):
                    chapters = [{"url": meta["read_url"], "label": "Глава 1"}]
                if not chapters:
                    raise ValueError("Не нашёл ссылок на главы — список глав может "
                                     "подгружаться скриптом.")
                return {"kind": "series", "title": meta.get("title", ""),
                        "description": meta.get("description", ""),
                        "cover": meta.get("cover", ""), "chapters": chapters,
                        "final_url": final_url}
            else:
                page_urls = extract_image_urls(page_html, final_url)[:_MAX_IMAGES]
                if not page_urls:
                    raise ValueError("На странице не найдено изображений.")
                return {"kind": "chapter", "title": meta.get("title", ""),
                        "description": meta.get("description", ""),
                        "cover": meta.get("cover", ""), "page_urls": page_urls,
                        "final_url": final_url}
    except httpx.HTTPStatusError as e:
        raise ValueError(f"Страница недоступна (HTTP {e.response.status_code}).")
    except ValueError:
        raise
    except Exception as e:
        raise ValueError(f"Не удалось загрузить страницу: {e}")


def download_chapter(client, chapter_url: str, paginate: bool = True,
                     budget: dict | None = None, on_page=None) -> list[tuple[str, bytes]]:
    """Скачать изображения одной главы (публичная, с колбэком прогресса)."""
    if budget is None:
        budget = {"total": 0}
    return _download_chapter(client, chapter_url, paginate, budget, on_page)


def extract_chapter_links(series_html: str, base_url: str) -> list[str]:
    """Список ссылок на главы со страницы серии, упорядоченный по номеру (возр.)."""
    try:
        doc = lhtml.fromstring(series_html.encode("utf-8"),
                               parser=lhtml.HTMLParser(encoding="utf-8"))
    except Exception:
        return []

    base_path = urlparse(base_url).path.rstrip("/")
    seen: set[str] = set()
    cands: list[str] = []
    for a in doc.xpath("//a[@href]"):
        href = a.get("href") or ""
        if href.startswith(("#", "javascript:", "mailto:")):
            continue
        absu = urljoin(base_url, href).split("#")[0]
        path = urlparse(absu).path
        tail = path[len(base_path):] if base_path and path.startswith(base_path + "/") else ""
        # глава = либо расширяет путь серии и содержит число, либо адрес «похож на главу»
        is_chapter = (tail and _NUM_RE.search(tail)) or _CHAPTER_HREF.search(absu)
        if is_chapter and absu not in seen and absu.rstrip("/") != base_url.rstrip("/"):
            seen.add(absu)
            cands.append(absu)

    def sort_key(u: str):
        tail = urlparse(u).path
        nums = [float(n.replace(",", ".")) for n in _NUM_RE.findall(tail)]
        return (nums or [0.0])
    cands.sort(key=sort_key)
    return cands[:_MAX_CHAPTERS]


def _download_chapter(client, chapter_url: str, paginate: bool, budget: dict, on_page=None) -> list[tuple[str, bytes]]:
    """Скачать изображения одной главы (с перелистыванием постраничных читалок)."""
    images: list[tuple[str, bytes]] = []
    seen_img: set[str] = set()
    visited: set[str] = set()
    try:
        r = client.get(chapter_url)
        if r.status_code != 200:
            return []
        cur_url, cur_html = str(r.url), r.text
    except Exception:
        return []

    subpages = 0
    while cur_url and subpages < (_MAX_SUBPAGES if paginate else 1):
        visited.add(cur_url)
        page_imgs = [u for u in extract_image_urls(cur_html, cur_url) if u not in seen_img]
        seen_img.update(page_imgs)
        if page_imgs:
            images += _download_images(client, page_imgs[:_MAX_IMAGES], cur_url, len(images) + 1, budget, on_page)
        if budget["total"] > _MAX_TOTAL_BYTES or len(images) >= _MAX_IMAGES:
            break
        if not paginate:
            break
        nxt = find_next_url(cur_html, cur_url, cur_url)
        if not nxt or nxt in visited:
            break
        try:
            r = client.get(nxt)
            if r.status_code != 200:
                break
            cur_url, cur_html = str(r.url), r.text
        except Exception:
            break
        subpages += 1

    if images and all(re.match(r"^\d+", n) for n, _ in images):
        images.sort(key=lambda p: _natural_key(p[0]))
    return images


def fetch_series(url: str, paginate: bool = True) -> dict:
    """Импорт всей серии: метаданные + все главы со страницы тайтла.

    Возвращает {title, description, cover_bytes, chapters: [(label, images), ...]}.
    """
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    headers = {"User-Agent": _UA, "Accept-Language": "ru,en;q=0.9"}

    try:
        with httpx.Client(follow_redirects=True, timeout=_PAGE_TIMEOUT, headers=headers) as client:
            resp = client.get(url)
            resp.raise_for_status()
            series_html, series_url = resp.text, str(resp.url)
            meta = extract_meta(series_html, series_url)

            cover_bytes = None
            if meta.get("cover"):
                try:
                    cr = client.get(meta["cover"], headers={**headers, "Referer": series_url})
                    if cr.status_code == 200 and cr.headers.get("content-type", "").startswith("image/") \
                            and len(cr.content) >= _MIN_BYTES:
                        cover_bytes = cr.content
                except Exception:
                    pass

            chapter_urls = extract_chapter_links(series_html, series_url)
            if not chapter_urls and meta.get("read_url"):
                chapter_urls = [meta["read_url"]]
            if not chapter_urls:
                raise ValueError("Не нашёл ссылок на главы на странице серии. "
                                 "Возможно, список глав подгружается скриптом.")

            budget = {"total": (len(cover_bytes) if cover_bytes else 0)}
            chapters = []
            for i, ch_url in enumerate(chapter_urls, start=1):
                imgs = _download_chapter(client, ch_url, paginate, budget)
                if imgs:
                    chapters.append((f"Глава {i}", imgs))
                if budget["total"] > _MAX_TOTAL_BYTES:
                    break
    except httpx.HTTPStatusError as e:
        raise ValueError(f"Страница недоступна (HTTP {e.response.status_code}).")
    except ValueError:
        raise
    except Exception as e:
        raise ValueError(f"Не удалось загрузить серию: {e}")

    if not chapters:
        raise ValueError("Главы найдены, но изображения скачать не удалось "
                         "(вероятно, картинки грузятся скриптом или стоит защита).")
    return {"title": meta.get("title", ""), "description": meta.get("description", ""),
            "cover_bytes": cover_bytes, "chapters": chapters}


def fetch_manga(url: str, follow_read: bool = False, paginate: bool = True) -> dict:
    """Скачать главу манги по ссылке.

    follow_read — сначала найти на странице серии ссылку «Читать» и описание/обложку.
    paginate    — листать постраничные читалки по ссылке «Следующая».
    Возвращает {images, title, description, cover_bytes}. Бросает ValueError при ошибке.
    """
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    headers = {"User-Agent": _UA, "Accept-Language": "ru,en;q=0.9"}

    title = description = ""
    cover_bytes = None
    images: list[tuple[str, bytes]] = []

    try:
        with httpx.Client(follow_redirects=True, timeout=_PAGE_TIMEOUT, headers=headers) as client:
            resp = client.get(url)
            resp.raise_for_status()
            series_html = resp.text
            series_url = str(resp.url)
            meta = extract_meta(series_html, series_url)
            title = meta.get("title", "")
            description = meta.get("description", "")

            # обложка из og:image
            cover_url = meta.get("cover")
            if cover_url:
                try:
                    cr = client.get(cover_url, headers={**headers, "Referer": series_url})
                    if cr.status_code == 200 and cr.headers.get("content-type", "").startswith("image/") \
                            and len(cr.content) >= _MIN_BYTES:
                        cover_bytes = cr.content
                except Exception:
                    pass

            # начальная страница чтения
            if follow_read and meta.get("read_url"):
                cur = meta["read_url"]
                resp = client.get(cur)
                resp.raise_for_status()
            cur_url = str(resp.url)
            cur_html = resp.text

            budget = {"total": (len(cover_bytes) if cover_bytes else 0)}
            seen_img: set[str] = set()
            visited_pages: set[str] = set()
            subpages = 0

            while cur_url and subpages < (_MAX_SUBPAGES if paginate else 1):
                visited_pages.add(cur_url)
                page_imgs = [u for u in extract_image_urls(cur_html, cur_url) if u not in seen_img]
                seen_img.update(page_imgs)
                if page_imgs:
                    images += _download_images(client, page_imgs[:_MAX_IMAGES],
                                               cur_url, len(images) + 1, budget)
                if budget["total"] > _MAX_TOTAL_BYTES or len(images) >= _MAX_IMAGES:
                    break
                if not paginate:
                    break
                nxt = find_next_url(cur_html, cur_url, cur_url)
                if not nxt or nxt in visited_pages:
                    break
                try:
                    r = client.get(nxt)
                    if r.status_code != 200:
                        break
                    cur_url, cur_html = str(r.url), r.text
                except Exception:
                    break
                subpages += 1
    except httpx.HTTPStatusError as e:
        raise ValueError(f"Страница недоступна (HTTP {e.response.status_code}).")
    except ValueError:
        raise
    except Exception as e:
        raise ValueError(f"Не удалось загрузить страницу: {e}")

    if images and all(re.match(r"^\d+", n) for n, _ in images):
        images.sort(key=lambda p: _natural_key(p[0]))
    if not images:
        raise ValueError("Не удалось скачать изображения — возможно, картинки грузятся "
                         "скриптом, стоит защита от хотлинка, или ссылка ведёт не на главу.")
    return {"images": images, "title": title, "description": description, "cover_bytes": cover_bytes}
