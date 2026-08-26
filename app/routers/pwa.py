"""Progressive Web App: манифест, service worker и иконки.

Все маршруты публичные (без авторизации) — иначе браузер не сможет
зарегистрировать SW и показать «Установить приложение» на странице логина.
"""
import json
import struct
import zlib
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, PlainTextResponse, Response

router = APIRouter()

THEME_COLOR = "#212529"   # как у тёмного навбара
BG_COLOR = "#f8f9fa"
ICON_RGB = (37, 99, 235)  # #2563eb

# ID вычисляется из открытого ключа подписи (extension/webook-extension-signing-key.pem,
# хранится вне репозитория) — стабилен, пока ключ не меняется. См. static/webook.crx.
EXTENSION_ID = "kbccpofjafdhlhhiejddeoapcnkooebh"


def _png_from_rgb(size: int, raw: bytearray) -> bytes:
    """Собрать PNG (truecolor) из готового буфера строк (с байтом-фильтром)."""
    def chunk(tag: bytes, data: bytes) -> bytes:
        crc = struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
        return struct.pack(">I", len(data)) + tag + data + crc

    ihdr = struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", ihdr)
        + chunk(b"IDAT", zlib.compress(bytes(raw), 9))
        + chunk(b"IEND", b"")
    )


def _icon_png(size: int) -> bytes:
    """Иконка-логотип (синяя открытая книга), как у приложения, без зависимостей.

    Full-bleed синий фон + две страницы (белая/светло-голубая) с зазором-корешком —
    повторяет `ic_launcher.xml`. Маскируемая иконка, поэтому без скруглений.
    """
    bg = (37, 99, 235)       # #2563EB
    white = (255, 255, 255)
    light = (219, 234, 254)  # #DBEAFE
    raw = bytearray()
    for y in range(size):
        raw.append(0)  # фильтр строки = None
        v = y * 108.0 / size
        for x in range(size):
            u = x * 108.0 / size
            # книга занимает x 30..78, y 28..82 (как в ic_launcher)
            if 30 <= u <= 78 and 28 <= v <= 82 and not (52.5 <= u <= 55.5):
                r, g, b = white if u < 54 else light
            else:
                r, g, b = bg
            raw += bytes((r, g, b))
    return _png_from_rgb(size, raw)


_ICON_SVG = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 108 108">'
    '<rect width="108" height="108" fill="#2563eb"/>'
    '<path fill="#ffffff" d="M30,30h30c4,0 6,2 6,6v42c0,-3 -3,-5 -6,-5h-30z"/>'
    '<path fill="#dbeafe" d="M78,30h-30c-4,0 -6,2 -6,6v42c0,-3 3,-5 6,-5h30z"/>'
    "</svg>"
)


@router.get("/manifest.webmanifest")
def manifest():
    data = {
        "name": "WeBook — библиотека",
        "short_name": "WeBook",
        "description": "Личная библиотека книг и статей",
        "start_url": "/dashboard",
        "scope": "/",
        "display": "standalone",
        "orientation": "portrait-primary",
        "background_color": BG_COLOR,
        "theme_color": THEME_COLOR,
        "lang": "ru",
        "icons": [
            {"src": "/icons/icon.svg", "sizes": "any", "type": "image/svg+xml", "purpose": "any maskable"},
            {"src": "/icons/icon-192.png", "sizes": "192x192", "type": "image/png", "purpose": "any maskable"},
            {"src": "/icons/icon-512.png", "sizes": "512x512", "type": "image/png", "purpose": "any maskable"},
        ],
    }
    return JSONResponse(data, media_type="application/manifest+json")


@router.get("/icons/icon.svg")
def icon_svg():
    return Response(content=_ICON_SVG, media_type="image/svg+xml",
                    headers={"Cache-Control": "public, max-age=604800"})


@router.get("/icons/icon-{size}.png")
def icon_png(size: int):
    if size not in (192, 512):
        size = 192
    return Response(content=_icon_png(size), media_type="image/png",
                    headers={"Cache-Control": "public, max-age=604800"})


_SW_JS = """
const CACHE = 'webook-v2';

self.addEventListener('install', () => self.skipWaiting());

self.addEventListener('activate', (e) => {
  e.waitUntil((async () => {
    const keys = await caches.keys();
    await Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k)));
    await self.clients.claim();
  })());
});

self.addEventListener('fetch', (e) => {
  const req = e.request;
  if (req.method !== 'GET') return;
  const url = new URL(req.url);
  if (url.origin !== location.origin) return;

  // Кэш статики и иконок: cache-first
  if (url.pathname.startsWith('/static') ||
      url.pathname.startsWith('/icons') ||
      url.pathname === '/manifest.webmanifest') {
    e.respondWith((async () => {
      const cached = await caches.match(req);
      if (cached) return cached;
      const res = await fetch(req);
      try { (await caches.open(CACHE)).put(req, res.clone()); } catch (_) {}
      return res;
    })());
    return;
  }

  // Остальное: network-first с откатом в кэш (офлайн)
  e.respondWith((async () => {
    try {
      const res = await fetch(req);
      return res;
    } catch (err) {
      const cached = await caches.match(req);
      if (cached) return cached;
      throw err;
    }
  })());
});

self.addEventListener('push', (e) => {
  let data = {};
  try { data = e.data ? e.data.json() : {}; } catch (_) {}
  const title = data.title || 'WeBook';
  const options = {
    body: data.body || '',
    icon: '/icons/icon-192.png',
    badge: '/icons/icon-192.png',
    data: { url: data.url || '/dashboard' },
  };
  e.waitUntil(self.registration.showNotification(title, options));
});

self.addEventListener('notificationclick', (e) => {
  e.notification.close();
  const url = (e.notification.data && e.notification.data.url) || '/dashboard';
  e.waitUntil((async () => {
    const all = await clients.matchAll({ type: 'window' });
    for (const c of all) { if (c.url.includes(url) && 'focus' in c) return c.focus(); }
    if (clients.openWindow) return clients.openWindow(url);
  })());
});
""".lstrip()


@router.get("/sw.js")
def service_worker():
    return Response(content=_SW_JS, media_type="application/javascript",
                    headers={"Cache-Control": "no-cache"})


def _extension_version() -> str:
    try:
        data = json.loads((Path("extension") / "manifest.json").read_text(encoding="utf-8"))
        return data.get("version", "1.0.0")
    except OSError:
        return "1.0.0"


@router.get("/extension/webook.crx")
def extension_crx():
    """Подписанный .crx — читает Chrome/Yandex при автоустановке через политику
    ExtensionSettings (см. /settings). Публичный маршрут: фоновый апдейтер браузера
    ходит сюда без cookie/сессии."""
    crx = Path("static") / "webook.crx"
    if not crx.exists():
        return PlainTextResponse("not built", status_code=404)
    return Response(content=crx.read_bytes(), media_type="application/x-chrome-extension",
                    headers={"Cache-Control": "no-cache"})


@router.get("/extension/updates.xml")
def extension_updates(request: Request):
    """Omaha update-манифест для ExtensionSettings.update_url. Публичный, без авторизации."""
    host = request.url.hostname or "localhost"
    codebase = f"http://{host}:8000/extension/webook.crx"
    xml = (
        "<?xml version='1.0' encoding='UTF-8'?>\n"
        "<gupdate xmlns='http://www.google.com/update2/response' protocol='2.0'>\n"
        f"  <app appid='{EXTENSION_ID}'>\n"
        f"    <updatecheck codebase='{codebase}' version='{_extension_version()}' />\n"
        "  </app>\n"
        "</gupdate>\n"
    )
    return Response(content=xml, media_type="application/xml", headers={"Cache-Control": "no-cache"})
