#!/usr/bin/env python3
"""Genesis dashboard: one small read only web server, standard library only.

Serves the command center pages, the offline maps (with HTTP range requests,
which PMTiles needs), the scripture library, phone apps, and a JSON API for
services, system health, vault contents and the survival layers.
"""

import json
import mimetypes
import os
import shutil
import socket
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlsplit

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from genesis import __version__, catalog, maps, poi  # noqa: E402
from genesis.util import read_json  # noqa: E402

STATIC = os.path.join(HERE, "static")
VAULT = os.path.abspath(os.environ.get("GENESIS_VAULT") or os.path.join(ROOT, "vault"))
# Folders of the vault that may be read over HTTP. Nothing else is reachable.
MOUNTS = {"maps": "maps", "scripture": "scripture", "apps": "apps", "documents": "documents"}
ANCIENT = ["hbo", "arc", "grc", "lat", "cop", "syc", "got", "chu", "gez"]
mimetypes.add_type("application/vnd.android.package-archive", ".apk")
mimetypes.add_type("application/octet-stream", ".pmtiles")
mimetypes.add_type("application/x-protobuf", ".pbf")
mimetypes.add_type("text/javascript", ".mjs")
mimetypes.add_type("font/woff2", ".woff2")

_cache, _cache_lock = {}, threading.Lock()


def cached(key, ttl, fn):
    now = time.time()
    with _cache_lock:
        hit = _cache.get(key)
        if hit and now - hit[0] < ttl:
            return hit[1]
    value = fn()
    with _cache_lock:
        _cache[key] = (now, value)
    return value


def _port_open(host, port):
    try:
        with socket.create_connection((host, port), timeout=0.6):
            return True
    except OSError:
        return False


def services():
    reg = read_json(os.path.join(HERE, "services.json"), [])

    def check(s):
        # Inside compose the service name resolves; on a bare host fall back to the published port.
        up = _port_open(s["host"], s["port"]) or _port_open("127.0.0.1", s["public"])
        return dict(s, up=up)
    with ThreadPoolExecutor(max_workers=16) as pool:
        return list(pool.map(check, reg))


def system():
    info = {"genesis": __version__, "cpus": os.cpu_count()}
    try:
        info["load"] = round(os.getloadavg()[0], 2)
    except OSError:
        pass
    try:
        mem = {}
        with open("/proc/meminfo") as f:
            for line in f:
                k, v = line.split(":", 1)
                mem[k] = int(v.split()[0]) * 1024
        info["ram_total"], info["ram_free"] = mem.get("MemTotal"), mem.get("MemAvailable")
        with open("/proc/uptime") as f:
            info["uptime"] = int(float(f.read().split()[0]))
    except (OSError, ValueError):
        pass
    temps = []
    base = "/sys/class/thermal"
    if os.path.isdir(base):
        for zone in os.listdir(base):
            try:
                with open(os.path.join(base, zone, "temp")) as f:
                    temps.append(int(f.read().strip()) / 1000.0)
            except (OSError, ValueError):
                pass
    if temps:
        info["temp_c"] = round(max(temps), 1)
    power = "/sys/class/power_supply"
    if os.path.isdir(power):
        for name in os.listdir(power):
            try:
                with open(os.path.join(power, name, "capacity")) as f:
                    info["battery"] = int(f.read().strip())
                with open(os.path.join(power, name, "status")) as f:
                    info["battery_status"] = f.read().strip()
            except (OSError, ValueError):
                pass
    probe = VAULT
    while not os.path.exists(probe):
        probe = os.path.dirname(probe)
    du = shutil.disk_usage(probe)
    info["disk_total"], info["disk_free"] = du.total, du.free
    return info


def vault():
    state = read_json(os.path.join(VAULT, ".genesis", "state.json"), {})
    meta = read_json(os.path.join(catalog.LOCK_DIR, "_meta.json"), {})
    locked = {iid: (pack, entry) for pack, iid, entry in catalog.all_locked()}
    packs = {}
    for iid, st in state.items():
        pack, entry = locked.get(iid, (st.get("pack", "other"), {}))
        p = packs.setdefault(pack, {"items": 0, "outdated": 0, "list": []})
        p["items"] += 1
        newest = entry.get("version")
        outdated = bool(newest and newest != st.get("version"))
        p["outdated"] += outdated
        p["list"].append({"id": iid, "title": entry.get("title") or iid, "version": st.get("version"),
                          "kind": entry.get("kind"), "outdated": outdated})
    for p in packs.values():
        p["list"].sort(key=lambda x: x["title"].lower())
    apps = sorted(f for f in os.listdir(os.path.join(VAULT, "apps"))) if os.path.isdir(os.path.join(VAULT, "apps")) else []
    docs = []
    for folder, _, files in os.walk(os.path.join(VAULT, "documents")):
        for name in sorted(files):
            docs.append(os.path.relpath(os.path.join(folder, name), VAULT).replace(os.sep, "/"))
    return {"catalog_refreshed": meta.get("refreshed"), "packs": packs, "apps": apps, "documents": docs,
            "config": read_json(os.path.join(VAULT, ".genesis", "config.json"), {})}


def scripture():
    out = []
    base = os.path.join(VAULT, "scripture")
    if os.path.isdir(base):
        for tid in os.listdir(base):
            info = read_json(os.path.join(base, tid, "genesis.json"))
            if info and os.path.exists(os.path.join(base, tid, "index.htm")):
                out.append({"id": tid, "title": info.get("name") or info.get("title"), "label": info.get("title"),
                            "language": info.get("language"), "native": info.get("native"), "lang": info.get("lang"),
                            "direction": info.get("direction"), "license": info.get("license"),
                            "ot": info.get("ot"), "nt": info.get("nt"), "dc": info.get("dc"),
                            "href": "/scripture/%s/index.htm" % tid})

    def order(t):
        lang = t.get("lang") or ""
        return (ANCIENT.index(lang) if lang in ANCIENT else len(ANCIENT), t.get("language") or "", t.get("label") or "")
    return sorted(out, key=order)


def map_list():
    found = maps.list_maps(VAULT)
    assets = os.path.isdir(os.path.join(VAULT, "maps", "assets", "fonts"))
    return {"maps": found, "assets": assets}


class Handler(BaseHTTPRequestHandler):
    server_version = "Genesis/" + __version__
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        if os.environ.get("GENESIS_LOG"):
            super().log_message(fmt, *args)

    def _json(self, data, status=200):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _file(self, base, rel):
        path = os.path.normpath(os.path.join(base, rel))
        if not (path + os.sep).startswith(os.path.abspath(base) + os.sep) or not os.path.isfile(path):
            return self._json({"error": "not found"}, 404)
        size = os.path.getsize(path)
        start, end, status = 0, size - 1, HTTPStatus.OK
        rng = self.headers.get("Range", "")
        if rng.startswith("bytes="):
            a, _, b = rng[6:].split(",")[0].partition("-")
            try:
                if a == "":
                    start = max(0, size - int(b))
                else:
                    start = int(a)
                    end = min(int(b), size - 1) if b else size - 1
            except ValueError:
                start, end = 0, size - 1
            if start > end or start >= size:
                self.send_response(HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE)
                self.send_header("Content-Range", "bytes */%d" % size)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return None
            status = HTTPStatus.PARTIAL_CONTENT
        ctype = mimetypes.guess_type(path)[0] or "application/octet-stream"
        if ctype.startswith("text/") or ctype in ("application/json", "text/javascript"):
            ctype += "; charset=utf-8"
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(end - start + 1))
        self.send_header("Accept-Ranges", "bytes")
        if status == HTTPStatus.PARTIAL_CONTENT:
            self.send_header("Content-Range", "bytes %d-%d/%d" % (start, end, size))
        self.send_header("Cache-Control", "no-cache" if base == STATIC else "public, max-age=3600")
        self.end_headers()
        if self.command == "HEAD":
            return None
        with open(path, "rb") as f:
            f.seek(start)
            left = end - start + 1
            while left > 0:
                chunk = f.read(min(1024 * 256, left))
                if not chunk:
                    break
                try:
                    self.wfile.write(chunk)
                except (BrokenPipeError, ConnectionResetError):
                    break
                left -= len(chunk)
        return None

    def _api(self, name, q):
        def num(key, default=None):
            try:
                return float(q[key][0])
            except (KeyError, ValueError, IndexError):
                return default
        if name == "services":
            return self._json(cached("services", 8, services))
        if name == "system":
            return self._json(cached("system", 4, system))
        if name == "vault":
            return self._json(cached("vault", 15, vault))
        if name == "scripture":
            return self._json(cached("scripture", 30, scripture))
        if name == "maps":
            return self._json(cached("maps", 15, map_list))
        if name == "poi/categories":
            return self._json(poi.categories())
        if name.startswith("poi/"):
            db = poi.connect(poi.default_db(VAULT))
            if db is None:
                return self._json({"error": "no survival layers installed", "places": []}, 200)
            try:
                cat = (q.get("cat") or [None])[0] or None
                if name == "poi/stats":
                    return self._json(cached("poistats", 300, lambda: poi.stats(db)))
                if name == "poi/nearest":
                    lat, lon = num("lat"), num("lon")
                    if lat is None or lon is None:
                        return self._json({"error": "lat and lon are required"}, 400)
                    n = int(min(50, num("n", 5)))
                    if (q.get("each") or [""])[0]:  # the n nearest of every category, for the bearing table
                        out = {c: poi.nearest(db, lat, lon, c, n) for c in poi.categories()}
                        return self._json({"by_category": out})
                    return self._json({"places": poi.nearest(db, lat, lon, cat, n)})
                if name == "poi/bbox":
                    box = [num(k) for k in ("west", "south", "east", "north")]
                    if None in box:
                        return self._json({"error": "west, south, east and north are required"}, 400)
                    return self._json({"places": poi.in_bbox(db, *box, cat=cat, limit=int(min(5000, num("limit", 1500))))})
            finally:
                db.close()
        return self._json({"error": "unknown endpoint"}, 404)

    def do_GET(self):
        url = urlsplit(self.path)
        path = unquote(url.path)
        try:
            if path.startswith("/api/"):
                return self._api(path[5:].strip("/"), parse_qs(url.query))
            first, _, rest = path.lstrip("/").partition("/")
            if first in MOUNTS:
                return self._file(os.path.join(VAULT, MOUNTS[first]), rest)
            rel = path.lstrip("/") or "index.html"
            return self._file(STATIC, rel)
        except (BrokenPipeError, ConnectionResetError):
            return None
        except Exception as e:  # never take the server down for one bad request
            return self._json({"error": "%s: %s" % (type(e).__name__, e)}, 500)

    do_HEAD = do_GET


def main():
    port = int(os.environ.get("GENESIS_PORT_INTERNAL", os.environ.get("PORT", "8080")))
    server = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    server.daemon_threads = True
    print("Genesis dashboard on port %d, vault %s" % (port, VAULT), flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
