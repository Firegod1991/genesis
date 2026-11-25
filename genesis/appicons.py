"""The app icon a phone puts on its home screen, drawn as PNG pictures, which iPhones and many Android phones want.

The picture is the one in dashboard/static/icons/app.svg: a sprout in a square, light on dark. It is drawn
here in code, with nothing but the standard library, so the repository holds no binary file that only a
build could make. Each size is drawn once, the first time a phone asks, and kept in memory.
"""

import math
import struct
import threading
import zlib

SIZES = {"icons/app-192.png": 192, "icons/app-512.png": 512, "icons/apple-touch-icon.png": 180}
INK, PAPER = (0x16, 0x20, 0x1B), (0xE6, 0xEA, 0xE1)
_cache, _lock = {}, threading.Lock()


def _lens(p, q, half_width):
    """A leaf: the part two equal discs share, with its tips at p and q."""
    mx, my = (p[0] + q[0]) / 2, (p[1] + q[1]) / 2
    dx, dy = q[0] - p[0], q[1] - p[1]
    a = math.hypot(dx, dy) / 2
    r = (a * a + half_width * half_width) / (2 * half_width)
    off = r - half_width
    nx, ny = -dy / (2 * a), dx / (2 * a)
    return (mx + nx * off, my + ny * off), (mx - nx * off, my - ny * off), r * r


# In the 24 unit square of app.svg: a frame, a stem and two leaves, all inside the middle 60 percent.
_FRAME = ((3.2, 20.8), (4.8, 19.2))
_STEM = (11.2, 12.8, 11.0, 18.0)
_LEAVES = (_lens((12.0, 11.2), (17.2, 6.4), 1.5), _lens((12.0, 13.7), (7.3, 10.0), 1.3))


def _lit(x, y):
    """True where the picture is light, for a point in the 24 unit square of app.svg."""
    u, v = (x - 4.8) / 0.6, (y - 4.8) / 0.6
    (o0, o1), (i0, i1) = _FRAME
    if o0 <= u <= o1 and o0 <= v <= o1 and not (i0 < u < i1 and i0 < v < i1):
        return True
    if _STEM[0] <= u <= _STEM[1] and _STEM[2] <= v <= _STEM[3]:
        return True
    for (c1, c2, rr) in _LEAVES:
        if (u - c1[0]) ** 2 + (v - c1[1]) ** 2 <= rr and (u - c2[0]) ** 2 + (v - c2[1]) ** 2 <= rr:
            return True
    return False


def draw(size, samples=3):
    """The icon as PNG bytes, size pixels square, its edges smoothed by sampling each pixel samples² times."""
    step, rows = 24.0 / size, []
    offsets = [(k + 0.5) / samples for k in range(samples)]
    for py in range(size):
        row = bytearray(b"\x00")  # filter type: none
        for px in range(size):
            hit = sum(_lit((px + ox) * step, (py + oy) * step) for oy in offsets for ox in offsets)
            t = hit / float(samples * samples)
            row += bytes(int(round(INK[c] + (PAPER[c] - INK[c]) * t)) for c in range(3))
        rows.append(bytes(row))

    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)

    header = struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0)  # 8 bit RGB
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IDAT", zlib.compress(b"".join(rows), 9))
            + chunk(b"IEND", b""))


def png(name):
    """The PNG for a served path such as icons/app-192.png, or None when the path is not an app icon."""
    if name not in SIZES:
        return None
    with _lock:
        if name not in _cache:
            _cache[name] = draw(SIZES[name])
        return _cache[name]


def warm():
    """Draw every size in the background, so the first phone to ask is not kept waiting."""
    threading.Thread(target=lambda: [png(n) for n in SIZES], daemon=True).start()
