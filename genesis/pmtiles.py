"""Read vector tiles out of a PMTiles map, with nothing but the standard library.

The maps on the drive are PMTiles archives of Mapbox Vector Tiles. The dashboard's map reads them in the
browser; this module reads the same tiles in Python, so Genesis can find a way along the roads in them.
It follows version 3 of the PMTiles format and version 2 of the vector tile format.
"""

import gzip
import math
import struct
import threading
import zlib
from collections import OrderedDict

MAGIC = b"PMTiles"
NONE, GZIP = 1, 2


def _varint(buf, pos):
    result, shift = 0, 0
    while True:
        b = buf[pos]
        pos += 1
        result |= (b & 0x7F) << shift
        if b < 0x80:
            return result, pos
        shift += 7


def _unpack(data, how):
    if how in (0, NONE):  # 0: unknown, which in practice means the bytes as they are
        return data
    if how == GZIP:
        return gzip.decompress(data) if data[:2] == b"\x1f\x8b" else zlib.decompress(data)
    raise ValueError("this map is compressed in a way Genesis cannot read (%d)" % how)


def zxy_to_tileid(z, x, y):
    """The position of a tile on the Hilbert curve PMTiles orders its tiles by."""
    if x >= 1 << z or y >= 1 << z or x < 0 or y < 0:
        raise ValueError("tile %d/%d/%d is outside its zoom level" % (z, x, y))
    acc, a, b = ((1 << z) * (1 << z) - 1) // 3, x, y
    i = z - 1
    s = 1 << i if z else 0
    while s > 0:
        rx, ry = a & s, b & s
        acc += ((3 * rx) ^ ry) * (1 << i)
        if ry == 0:
            a, b = (s - 1 - b, s - 1 - a) if rx != 0 else (b, a)
        s >>= 1
        i -= 1
    return acc


def _directory(data):
    n, pos = _varint(data, 0)
    ids, runs, lengths, offsets = [0] * n, [0] * n, [0] * n, [0] * n
    last = 0
    for i in range(n):
        v, pos = _varint(data, pos)
        last += v
        ids[i] = last
    for i in range(n):
        runs[i], pos = _varint(data, pos)
    for i in range(n):
        lengths[i], pos = _varint(data, pos)
    for i in range(n):
        v, pos = _varint(data, pos)
        offsets[i] = offsets[i - 1] + lengths[i - 1] if v == 0 and i > 0 else v - 1
    return ids, runs, lengths, offsets


class Archive:
    """One PMTiles file. Directories are kept as they are read, tiles are read on demand."""

    def __init__(self, path):
        self.path = path
        self._lock, self._dirs = threading.Lock(), OrderedDict()
        with open(path, "rb") as f:
            head = f.read(127)
        if len(head) < 127 or head[:7] != MAGIC:
            raise ValueError("%s is not a PMTiles map" % path)
        if head[7] != 3:
            raise ValueError("%s is PMTiles version %d; Genesis reads version 3" % (path, head[7]))
        (self.root_offset, self.root_length, _, _, self.leaf_offset, _, self.data_offset, _,
         _, _, _) = struct.unpack_from("<11Q", head, 8)
        self.internal, self.compression, self.tile_type = head[97], head[98], head[99]
        self.minzoom, self.maxzoom = head[100], head[101]
        lon0, lat0, lon1, lat1 = struct.unpack_from("<4i", head, 102)
        self.bounds = (lon0 / 1e7, lat0 / 1e7, lon1 / 1e7, lat1 / 1e7)

    def _read(self, offset, length):
        with open(self.path, "rb") as f:
            f.seek(offset)
            return f.read(length)

    def _dir(self, offset, length):
        key = (offset, length)
        with self._lock:
            if key in self._dirs:
                self._dirs.move_to_end(key)
                return self._dirs[key]
        found = _directory(_unpack(self._read(offset, length), self.internal))
        with self._lock:
            self._dirs[key] = found
            while len(self._dirs) > 64:
                self._dirs.popitem(last=False)
        return found

    def tile(self, z, x, y):
        """The tile's bytes as stored, uncompressed, or None when the map has no such tile."""
        if not self.minzoom <= z <= self.maxzoom:
            return None
        want = zxy_to_tileid(z, x, y)
        offset, length = self.root_offset, self.root_length
        for _ in range(4):  # the root, then at most three levels of leaf directories
            ids, runs, lengths, offsets = self._dir(offset, length)
            lo, hi = 0, len(ids) - 1
            at = -1
            while lo <= hi:  # the last entry that starts at or before the tile
                mid = (lo + hi) // 2
                if ids[mid] <= want:
                    at, lo = mid, mid + 1
                else:
                    hi = mid - 1
            if at < 0:
                return None
            if runs[at] == 0:  # a leaf directory
                offset, length = self.leaf_offset + offsets[at], lengths[at]
                continue
            if want - ids[at] >= runs[at]:
                return None
            return _unpack(self._read(self.data_offset + offsets[at], lengths[at]), self.compression)
        return None


# ------------------------------------------------------------------ vector tiles

def _fields(buf):
    """(field number, wire type, value) for each field of a protobuf message."""
    pos, end = 0, len(buf)
    while pos < end:
        key, pos = _varint(buf, pos)
        field, wire = key >> 3, key & 7
        if wire == 0:
            value, pos = _varint(buf, pos)
        elif wire == 2:
            size, pos = _varint(buf, pos)
            value, pos = buf[pos:pos + size], pos + size
        elif wire == 1:
            value, pos = buf[pos:pos + 8], pos + 8
        elif wire == 5:
            value, pos = buf[pos:pos + 4], pos + 4
        else:
            raise ValueError("not a vector tile")
        yield field, wire, value


def _packed(buf):
    out, pos, end = [], 0, len(buf)
    while pos < end:
        v, pos = _varint(buf, pos)
        out.append(v)
    return out


def _value(buf):
    for field, _, v in _fields(buf):
        if field == 1:
            return v.decode("utf-8", "replace")
        if field == 2:
            return struct.unpack("<f", v)[0]
        if field == 3:
            return struct.unpack("<d", v)[0]
        if field in (4, 5):
            return v if field == 5 or v < 1 << 63 else v - (1 << 64)
        if field == 6:
            return (v >> 1) ^ -(v & 1)
        if field == 7:
            return bool(v)
    return None


def _lines(geometry):
    """The lines of a line feature, in tile units, one list of (x, y) for each."""
    out, line, x, y, i, n = [], None, 0, 0, 0, len(geometry)
    while i < n:
        cmd, count = geometry[i] & 7, geometry[i] >> 3
        i += 1
        if cmd in (1, 2):
            for _ in range(count):
                dx, dy = geometry[i], geometry[i + 1]
                i += 2
                x += (dx >> 1) ^ -(dx & 1)
                y += (dy >> 1) ^ -(dy & 1)
                if cmd == 1:
                    line = [(x, y)]
                    out.append(line)
                else:
                    line.append((x, y))
        elif cmd == 7 and line:
            line.append(line[0])
    return out


def layer_lines(data, name):
    """The line features of one layer of a vector tile, as (properties, lines, extent)."""
    for field, _, layer in _fields(data):
        if field != 3:
            continue
        lname, features, keys, values, extent = None, [], [], [], 4096
        for f, _, v in _fields(layer):
            if f == 1:
                lname = v.decode("utf-8", "replace")
            elif f == 2:
                features.append(v)
            elif f == 3:
                keys.append(v.decode("utf-8", "replace"))
            elif f == 4:
                values.append(v)
            elif f == 5:
                extent = v
        if lname != name:
            continue
        decoded = {}
        for feature in features:
            tags, kind, geometry = [], 0, []
            for f, _, v in _fields(feature):
                if f == 2:
                    tags = _packed(v)
                elif f == 3:
                    kind = v
                elif f == 4:
                    geometry = _packed(v)
            if kind != 2:  # lines only
                continue
            props = {}
            for k in range(0, len(tags) - 1, 2):
                vi = tags[k + 1]
                if vi not in decoded:
                    decoded[vi] = _value(values[vi])
                props[keys[tags[k]]] = decoded[vi]
            yield props, _lines(geometry), extent


def lonlat_to_tile(lon, lat, z):
    """The tile at zoom z that holds a point, as (x, y)."""
    n = 1 << z
    lat = max(-85.05112878, min(85.05112878, lat))
    x = int((lon + 180.0) / 360.0 * n)
    y = int((1.0 - math.log(math.tan(math.radians(lat)) + 1 / math.cos(math.radians(lat))) / math.pi) / 2.0 * n)
    return min(n - 1, max(0, x)), min(n - 1, max(0, y))


def unit_to_lonlat(gx, gy, size):
    """A point in tile units across the whole world (size units wide) as (lon, lat)."""
    lon = gx / size * 360.0 - 180.0
    lat = math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * gy / size))))
    return lon, lat
