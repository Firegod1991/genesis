"""Small maps made in the tests: vector tiles of a few streets, packed into a PMTiles file, as the
maps on a drive are. Only what the tests need of both formats is written here."""

import gzip
import struct

from genesis import pmtiles


def varint(n):
    out = bytearray()
    while True:
        b = n & 0x7F
        n >>= 7
        if n:
            out.append(b | 0x80)
        else:
            out.append(b)
            return bytes(out)


def field(number, wire, payload):
    key = varint((number << 3) | wire)
    if wire == 0:
        return key + varint(payload)
    return key + varint(len(payload)) + payload


def zigzag(n):
    return (n << 1) ^ (n >> 63)


def line_geometry(points):
    out, x, y = [], 0, 0
    for i, (px, py) in enumerate(points):
        if i == 0:
            out.append((1 & 7) | (1 << 3))
        elif i == 1:
            out.append((2 & 7) | ((len(points) - 1) << 3))
        out += [zigzag(px - x), zigzag(py - y)]
        x, y = px, py
    return out


def tile(layers, extent=4096):
    """layers: {name: [(properties, [(x, y), ...]), ...]} in tile units."""
    data = b""
    for name, features in layers.items():
        keys, values, body = [], [], b""
        for props, points in features:
            tags = []
            for k, v in props.items():
                if k not in keys:
                    keys.append(k)
                enc = field(7, 0, int(v)) if isinstance(v, bool) else field(1, 2, str(v).encode())
                if enc not in values:
                    values.append(enc)
                tags += [keys.index(k), values.index(enc)]
            feat = field(2, 2, b"".join(varint(t) for t in tags)) + field(3, 0, 2) + \
                field(4, 2, b"".join(varint(g) for g in line_geometry(points)))
            body += field(2, 2, feat)
        layer = field(15, 0, 2) + field(1, 2, name.encode()) + body
        layer += b"".join(field(3, 2, k.encode()) for k in keys) + b"".join(field(4, 2, v) for v in values)
        layer += field(5, 0, extent)
        data += field(3, 2, layer)
    return data


def directory(entries):
    """entries: [(tile id, run length, offset, length)], sorted by tile id."""
    out = varint(len(entries))
    last = 0
    for tid, _, _, _ in entries:
        out += varint(tid - last)
        last = tid
    out += b"".join(varint(run) for _, run, _, _ in entries)
    out += b"".join(varint(length) for _, _, _, length in entries)
    prev = None
    for tid, run, offset, length in entries:
        out += varint(0 if prev and offset == prev[0] + prev[1] else offset + 1)
        prev = (offset, length)
    return out


def write(path, tiles, zoom, bounds=(-180, -85, 180, 85), leaf=False):
    """Write {(x, y): tile bytes} at one zoom into a PMTiles file. With leaf, through a leaf directory."""
    blobs, entries, offset = b"", [], 0
    for (x, y), data in sorted(tiles.items(), key=lambda kv: pmtiles.zxy_to_tileid(zoom, *kv[0])):
        packed = gzip.compress(data)
        entries.append((pmtiles.zxy_to_tileid(zoom, x, y), 1, offset, len(packed)))
        blobs += packed
        offset += len(packed)
    leaves = b""
    if leaf:
        leaves = gzip.compress(directory(entries))
        root = gzip.compress(directory([(entries[0][0], 0, 0, len(leaves))]))
    else:
        root = gzip.compress(directory(entries))
    root_at = 127
    meta_at = root_at + len(root)
    meta = gzip.compress(b"{}")
    leaf_at = meta_at + len(meta)
    data_at = leaf_at + len(leaves)
    head = b"PMTiles" + bytes([3]) + struct.pack(
        "<11Q", root_at, len(root), meta_at, len(meta), leaf_at, len(leaves), data_at, len(blobs),
        len(tiles), len(tiles), len(tiles))
    head += bytes([1, 2, 2, 1, zoom, zoom])
    head += struct.pack("<4i", *[int(v * 1e7) for v in bounds]) + bytes([zoom]) + struct.pack("<2i", 0, 0)
    assert len(head) == 127
    with open(path, "wb") as f:
        f.write(head + root + meta + leaves + blobs)


def streets(lines, zoom, extent=4096):
    """Lay streets given in units across the whole world at this zoom into the tiles they cross.
    lines: [(properties, [(gx, gy), ...])]. Returns {(x, y): tile bytes}."""
    by_tile = {}
    for props, pts in lines:
        xs, ys = [p[0] for p in pts], [p[1] for p in pts]
        for tx in range(min(xs) // extent, max(xs) // extent + 1):
            for ty in range(min(ys) // extent, max(ys) // extent + 1):
                local = [(x - tx * extent, y - ty * extent) for x, y in pts]
                by_tile.setdefault((tx, ty), []).append((props, local))
    return {k: tile({"roads": v}, extent) for k, v in by_tile.items()}


def lonlat(gx, gy, zoom, extent=4096):
    return pmtiles.unit_to_lonlat(gx, gy, extent << zoom)
