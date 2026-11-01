"""Распаковка архивов с книгами: ZIP, 7z, RAR (несжатый), вложенные, cp866-имена, мусор."""
import io
import struct
import zipfile
import zlib

from app.services.book_service import expand_archive, archive_kind

FB = b'<?xml version="1.0"?><FictionBook xmlns="http://www.gribuser.ru/xml/fictionbook/2.0"/>'
ALLOWED = {".fb2", ".epub", ".pdf"}


def _zip(entries):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, data in entries:
            z.writestr(name, data)
    return buf.getvalue()


def test_zip_with_nested_zip_and_junk():
    inner = _zip([("inner.fb2", FB)])
    data = _zip([("a/one.fb2", FB), ("two.epub", b"x"), ("readme.txt", b"no"),
                 ("__MACOSX/._one.fb2", FB), ("nested.fb2.zip", inner)])
    items, errors = expand_archive(io.BytesIO(data), ALLOWED, 10 ** 8)
    assert sorted(n for n, _ in items) == ["inner.fb2", "one.fb2", "two.epub"]
    assert errors == []


def test_zip_cp866_names_without_utf8_flag():
    name = "Лукьяненко.fb2"
    placeholder = "X" * len(name.encode("cp866"))
    raw = _zip([(placeholder, FB)]).replace(placeholder.encode(), name.encode("cp866"))
    items, _ = expand_archive(io.BytesIO(raw), ALLOWED, 10 ** 8)
    assert [n for n, _ in items] == [name]


def test_size_limit_reported():
    items, errors = expand_archive(io.BytesIO(_zip([("big.fb2", b"x" * 2000)])), ALLOWED, 1000)
    assert items == [] and errors and "больше" in errors[0]


def test_7z_with_nested_zip():
    import py7zr
    buf = io.BytesIO()
    with py7zr.SevenZipFile(buf, "w") as z:
        z.writestr(FB, "Папка/книга1.fb2")
        z.writestr(_zip([("in_zip.fb2", FB)]), "nested.zip")
    buf.seek(0)
    assert archive_kind(buf) == "7z"
    items, errors = expand_archive(buf, ALLOWED, 10 ** 8)
    assert sorted(n for n, _ in items) == ["in_zip.fb2", "книга1.fb2"] and errors == []


def _rar4_stored(name: str, data: bytes) -> bytes:
    """Минимальный RAR 4.x с несжатым файлом — rarfile читает такой без внешних утилит."""
    def block(htype, flags, body):
        size = 7 + len(body)
        hdr = struct.pack("<BHH", htype, flags, size) + body
        return struct.pack("<H", zlib.crc32(hdr) & 0xFFFF) + hdr
    marker = b"Rar!\x1a\x07\x00"
    main = block(0x73, 0, b"\x00" * 6)
    fname = name.encode("utf-8")
    body = struct.pack("<IIBIIBBHI", len(data), len(data), 2, zlib.crc32(data), 0x5A000000, 20, 0x30, len(fname), 0x20) + fname
    return marker + main + block(0x74, 0x8000 | 0x0200 if False else 0x8000, body) + data + b"\xc4\x3d\x7b\x00\x40\x07\x00"


def test_rar_stored():
    raw = _rar4_stored("book.fb2", FB)
    assert archive_kind(io.BytesIO(raw)) == "rar"
    items, errors = expand_archive(io.BytesIO(raw), ALLOWED, 10 ** 8)
    assert [n for n, _ in items] == ["book.fb2"] and items[0][1] == FB, errors


def test_not_an_archive():
    items, errors = expand_archive(io.BytesIO(b"hello world"), ALLOWED, 10 ** 8)
    assert items == [] and "не архив" in errors[0]
