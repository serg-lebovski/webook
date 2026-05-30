"""Progressive Web App: манифест, service worker и иконки.

Все маршруты публичные (без авторизации) — иначе браузер не сможет
зарегистрировать SW и показать «Установить приложение» на странице логина.
"""
import struct
import zlib

from fastapi import APIRouter
from fastapi.responses import JSONResponse, Response

router = APIRouter()

THEME_COLOR = "#212529"   # как у тёмного навбара
BG_COLOR = "#f8f9fa"
ICON_RGB = (37, 99, 235)  # #2563eb


def _solid_png(size: int, rgb: tuple) -> bytes:
    """Минимальный сплошной PNG заданного цвета без внешних зависимостей."""
    r, g, b = rgb
    scanline = b"\x00" + bytes([r, g, b] * size)
    raw = scanline * size

    def chunk(tag: bytes, data: bytes) -> bytes:
        crc = struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
        return struct.pack(">I", len(data)) + tag + data + crc

    ihdr = struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", ihdr)
        + chunk(b"IDAT", zlib.compress(raw, 9))
        + chunk(b"IEND", b"")
    )


_ICON_SVG = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 512 512">'
    '<rect width="512" height="512" rx="96" fill="#2563eb"/>'
    '<path fill="#ffffff" d="M150 130h95c20 0 34 8 41 22 7-14 21-22 41-22h95v240'
    'h-95c-18 0-30 6-36 18-6-12-18-18-36-18h-95c-18 0-30 6-36 18-6-12-18-18-36-18'
    'h-3V130z" opacity="0.95"/>'
    '<path fill="#2563eb" d="M256 174v196" stroke="#2563eb" stroke-width="10"/>'
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
    return Response(content=_solid_png(size, ICON_RGB), media_type="image/png",
                    headers={"Cache-Control": "public, max-age=604800"})


_SW_JS = """
const CACHE = 'webook-v1';

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
""".lstrip()


@router.get("/sw.js")
def service_worker():
    return Response(content=_SW_JS, media_type="application/javascript",
                    headers={"Cache-Control": "no-cache"})
