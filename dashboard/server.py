#!/usr/bin/env python3
"""Genesis dashboard: one small web server, standard library only.

Serves the pages, the offline maps (with HTTP range requests, which PMTiles
needs), the manuals with their covers, the scripture library, phone apps and
programs, and a JSON API: services, system health, what is on the drive, the
survival layers, search across the library, and the local assistant. It reads
the vault and writes one place only: the notes people keep on the box. The safe
is never opened from here: this server says whether one exists, and nothing more.
"""

import http.client
import json
import mimetypes
import os
import re
import shutil
import socket
import sys
import threading
import time
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, quote, unquote, urlencode, urlsplit

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from genesis import __version__, catalog, maps, poi, safe  # noqa: E402
from genesis.util import memory, read_json, uptime  # noqa: E402

STATIC = os.path.join(HERE, "static")
VAULT = os.path.abspath(os.environ.get("GENESIS_VAULT") or os.path.join(ROOT, "vault"))
# Folders of the vault that may be read over HTTP. Nothing else is reachable.
MOUNTS = {"maps": "maps", "scripture": "scripture", "apps": "apps", "documents": "documents", "tools": "tools",
          "isos": "isos", "covers": os.path.join(".genesis", "covers")}
NOTES = os.path.join(VAULT, "files", "notes")
NOTE_NAME = re.compile(r"[\w][\w .()-]{0,80}\.(md|txt)")
# Where the book reader and the model server answer: inside compose by name, on a bare box by port.
# Started by `genesis start` there is no compose network, and a name is never looked up: with no
# network behind it, looking one up can hang for seconds on every click.
BARE = os.environ.get("GENESIS_BARE") == "1"
KIWIX = [("127.0.0.1", 8081)] if BARE else [("kiwix", 8080), ("127.0.0.1", 8081)]
OLLAMA = [("127.0.0.1", 11434)] if BARE else [("127.0.0.1", 11434), ("ollama", 11434)]
# Full text search reads these books first: the ones a person in trouble needs an answer from.
SEARCH_FIRST = ("survival", "medical")
SEARCH_BOOKS, SEARCH_WAIT = 24, 12
# What each kind of model on the drive is for, plainest first. The first match names it.
MODELS = (("ocr", None), ("embed", None), ("coder", "writing code"), ("code", "writing code"),
          ("translate", "translating"), ("medgemma", "medical questions"), ("deepseek-r1", "hard problems, slowly"))
ASSISTANT = ("You are the Genesis assistant. You run on this box with no internet. Be practical, plain and brief. "
             "When excerpts from the library are given, answer from them and name the book each fact came from. "
             "If the library does not cover the question, say so, then answer from what you know and say that it is "
             "from memory. Never guess about doses, wild plants or mushrooms: point to the manual instead.")
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
        up = (not BARE and _port_open(s["host"], s["port"])) or _port_open("127.0.0.1", s["public"])
        return dict(s, up=up)
    with ThreadPoolExecutor(max_workers=16) as pool:
        return list(pool.map(check, reg))


def system():
    info = {"genesis": __version__, "cpus": os.cpu_count()}
    try:
        info["load"] = round(os.getloadavg()[0], 2)
    except (OSError, AttributeError):  # Windows has no load average at all
        pass
    total, free = memory()
    if total:
        info["ram_total"], info["ram_free"] = total, free
    up = uptime()
    if up is not None:
        info["uptime"] = up
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
        if not st.get("files") and not st.get("model"):
            continue  # a record an older Genesis left for an item that put nothing on the drive
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
    # Names that start with a dot are the folder pictures and other housekeeping, never content.
    apps = sorted(f for f in os.listdir(os.path.join(VAULT, "apps")) if not f.startswith(".")) if os.path.isdir(os.path.join(VAULT, "apps")) else []
    docs = []
    for folder, _, files in os.walk(os.path.join(VAULT, "documents")):
        for name in sorted(files):
            if not name.startswith("."):
                docs.append(os.path.relpath(os.path.join(folder, name), VAULT).replace(os.sep, "/"))
    return {"catalog_refreshed": meta.get("refreshed"), "packs": packs, "apps": apps, "documents": docs,
            "config": read_json(os.path.join(VAULT, ".genesis", "config.json"), {})}


def _notes_of_packs():
    out = {}
    for name in catalog.pack_names():
        for item in catalog.load_pack(name)["items"]:
            if item.get("note") and item.get("id"):
                out[item["id"]] = item["note"]
    return out


def shelves():
    """The manuals on the drive, shelf by shelf: title, what each one is, and its cover when one was drawn."""
    known = {}
    for _, iid, entry in catalog.all_locked():
        if str(entry.get("dest", "")).startswith("documents"):
            for f in entry.get("files", []):
                known[entry["dest"] + "/" + f["filename"]] = (iid, entry)
    notes, out = cached("packnotes", 600, _notes_of_packs), {}
    for folder, _, files in os.walk(os.path.join(VAULT, "documents")):
        for name in sorted(files):
            if name.startswith(".") or name.endswith(".part"):
                continue
            path = os.path.join(folder, name)
            rel = os.path.relpath(path, VAULT).replace(os.sep, "/")
            iid, entry = known.get(rel, (None, {}))
            cover = rel.rsplit(".", 1)[0] + ".jpg"
            out.setdefault(rel.split("/")[1] if rel.count("/") > 1 else "other", []).append({
                "id": iid, "file": name, "href": "/" + quote(rel), "title": entry.get("title") or name,
                "note": notes.get(iid, ""), "license": entry.get("license"), "bytes": os.path.getsize(path),
                "cover": "/covers/" + quote(cover) if os.path.isfile(
                    os.path.join(VAULT, ".genesis", "covers", *cover.split("/"))) else None})
    return out


def _first_open(candidates):
    for host, port in candidates:
        if _port_open(host, port):
            return host, port
    return None


def _get(where, path, timeout=8):
    conn = http.client.HTTPConnection(where[0], where[1], timeout=timeout)
    try:
        conn.request("GET", path, headers={"Accept": "*/*"})
        r = conn.getresponse()
        return r.status, r.read()
    finally:
        conn.close()


def _reader_catalog():
    """What the book reader says about each book: its title, a line about it, its picture, and where it opens."""
    where = _first_open(KIWIX)
    if not where:
        return None
    try:
        status, body = _get(where, "/catalog/v2/entries?count=-1", timeout=30)
        root = ET.fromstring(body) if status == 200 else None
    except (OSError, ET.ParseError, http.client.HTTPException):
        return None
    out, ns = {}, "{http://www.w3.org/2005/Atom}"
    for e in root.iter(ns + "entry") if root is not None else []:
        info = {"title": e.findtext(ns + "title"), "about": e.findtext(ns + "summary") or "",
                "search": "_ftindex:yes" in (e.findtext(ns + "tags") or "")}
        for link in e.iter(ns + "link"):
            href = link.get("href", "")
            if link.get("type") == "text/html":
                info["path"] = href
            elif "thumbnail" in link.get("rel", ""):
                info["icon"] = href
        if info.get("path"):
            out[info["path"].rstrip("/").rsplit("/", 1)[-1]] = info
    return out


def books():
    """Every book on the drive. With the reader running each one has a picture and opens with a click."""
    locked = {}
    for pack, iid, entry in catalog.all_locked():
        if entry.get("kind") == "zim":
            for f in entry.get("files", []):
                locked[f["filename"]] = (pack, iid, entry)
    live, out, folder = cached("reader", 60, _reader_catalog), [], os.path.join(VAULT, "zim")
    for name in sorted(os.listdir(folder)) if os.path.isdir(folder) else []:
        if not name.endswith(".zim"):
            continue
        stem = name[:-4]
        pack, iid, entry = locked.get(name, ("other", stem, {}))
        k = (live or {}).get(stem, {})
        out.append({"id": iid, "name": stem, "pack": pack, "title": k.get("title") or entry.get("title") or stem,
                    "about": k.get("about", ""), "icon": k.get("icon"), "path": k.get("path"), "search": k.get("search", False),
                    "bytes": os.path.getsize(os.path.join(folder, name)), "tags": entry.get("tags", [])})
    return {"reader": live is not None, "books": out}


def software():
    """Programs for other computers and apps for phones, with the names people know them by."""
    known = {}
    for pack, iid, entry in catalog.all_locked(["tools", "apps", "isos"]):
        for f in entry.get("files", []):
            known[f["filename"]] = (pack, iid, entry, f)
    notes, out = cached("packnotes", 600, _notes_of_packs), {"tools": [], "apps": [], "isos": []}
    for kind in out:
        folder = os.path.join(VAULT, kind)
        for name in sorted(os.listdir(folder)) if os.path.isdir(folder) else []:
            path = os.path.join(folder, name)
            if not os.path.isfile(path) or name.startswith(".") or name.endswith(".part"):
                continue
            _, iid, entry, f = known.get(name, (kind, None, {}, {}))
            out[kind].append({"file": name, "href": "/%s/%s" % (kind, quote(name)), "title": entry.get("title") or name,
                              "note": notes.get(iid, ""), "platform": f.get("platform"), "bytes": os.path.getsize(path)})
    return out


def _score(words, *texts):
    text = " ".join(t for t in texts if t).lower()
    return sum((3 if re.search(r"\b%s" % re.escape(w), text) else 1) for w in words if w in text)


def _search_book(where, book, q):
    try:
        status, body = _get(where, "/search?" + urlencode({"pattern": q, "books.name": book["name"],
                                                            "pageLength": 3, "format": "xml"}), timeout=SEARCH_WAIT)
        root = ET.fromstring(body) if status == 200 else None
    except (OSError, ET.ParseError, http.client.HTTPException):
        return []
    out = []
    for item in root.iter("item") if root is not None else []:
        about = item.find("description")  # the reader marks the words it found with <b>, as tags of its own
        text = re.sub(r"<[^>]+>", "", "".join(about.itertext())) if about is not None else ""
        out.append({"kind": "page", "title": item.findtext("title"), "path": item.findtext("link"),
                    "text": " ".join(text.split()).strip(". ")[:320], "book": book["title"]})
    return out


def _inside_books(q, limit):
    """Full text search inside the books, through the reader: the survival and medical shelves first, then the
    encyclopedia. Quietly nothing when the reader is not running, and never a long wait on a slow disk."""
    where = _first_open(KIWIX)
    if not where:
        return []
    shelf = [b for b in books()["books"] if b["path"] and b["search"]]
    first = [b for b in shelf if b["pack"] in SEARCH_FIRST] + [b for b in shelf if b["name"].startswith("wikipedia_en_all")]
    pool, until, found = ThreadPoolExecutor(4), time.time() + SEARCH_WAIT, []
    asked = [pool.submit(_search_book, where, b, q) for b in first[:SEARCH_BOOKS]]
    for a in asked:
        try:
            found.append(a.result(timeout=max(0.05, until - time.time())))
        except Exception:  # still reading when the wait ran out: go on with what the others found
            found.append([])
    pool.shutdown(wait=False, cancel_futures=True)
    # The best page of every book before the second best of any.
    return [hits[i] for i in range(3) for hits in found if i < len(hits)][:limit]


def find(q):
    """One question, every shelf: manuals and books by what they are called, then the pages inside the books."""
    words = [w for w in re.findall(r"\w+", q.lower()) if len(w) > 2]
    if not words:
        return {"q": q, "hits": []}
    hits = []
    for shelf, items in cached("shelves", 30, shelves).items():
        for d in items:
            score = _score(words, d["title"], d["note"], shelf)
            if score:
                hits.append((score + 2, {"kind": "manual", "title": d["title"], "href": d["href"], "text": d["note"], "shelf": shelf}))
    for b in books()["books"]:
        score = _score(words, b["title"], b["about"], " ".join(b["tags"]))
        if score:
            hits.append((score, {"kind": "book", "title": b["title"], "path": b["path"], "text": b["about"], "icon": b["icon"]}))
    hits.sort(key=lambda h: -h[0])
    return {"q": q, "hits": [h for _, h in hits[:12]] + _inside_books(q, 10)}


def notes_list():
    out = []
    for name in sorted(os.listdir(NOTES)) if os.path.isdir(NOTES) else []:
        path = os.path.join(NOTES, name)
        if NOTE_NAME.fullmatch(name) and os.path.isfile(path):
            out.append({"name": name, "bytes": os.path.getsize(path), "changed": int(os.path.getmtime(path))})
    return sorted(out, key=lambda n: -n["changed"])


def scripture():
    out = []
    base = os.path.join(VAULT, "scripture")
    if os.path.isdir(base):
        for tid in os.listdir(base):
            if tid.startswith(".") or not os.path.isdir(os.path.join(base, tid)):
                continue
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
        if name == "shelves":
            return self._json(cached("shelves", 30, shelves))
        if name == "books":
            return self._json(books())
        if name == "software":
            return self._json(cached("software", 30, software))
        if name == "find":
            return self._json(find((q.get("q") or [""])[0].strip()[:200]))
        if name == "notes":
            return self._json(notes_list())
        if name == "safe":
            return self._json(cached("safe", 5, lambda: safe.status(VAULT)))
        if name.startswith("notes/"):
            note = name[6:]
            path = os.path.join(NOTES, note)
            if not NOTE_NAME.fullmatch(note) or not os.path.isfile(path):
                return self._json({"error": "no such note"}, 404)
            with open(path, encoding="utf-8", errors="replace") as f:
                return self._json({"name": note, "text": f.read()})
        if name == "ai/models":
            where = _first_open(OLLAMA)
            if not where:
                return self._json({"running": False, "models": []})
            try:
                status, body = _get(where, "/api/tags")
                found = json.loads(body).get("models", []) if status == 200 else []
            except (OSError, ValueError, http.client.HTTPException):
                found = []
            models = []
            for m in found:
                use = next((use for word, use in MODELS if word in m["name"]), "everyday questions")
                if use:  # models that only read pictures or turn text into numbers cannot hold a conversation
                    models.append({"name": m["name"], "bytes": m.get("size"), "use": use})
            # The one a person starts on is the smallest that answers everyday questions: it is the quickest.
            models.sort(key=lambda m: (m["use"] != "everyday questions", m["bytes"] or 0))
            return self._json({"running": True, "models": models})
        if name == "poi/categories":
            return self._json(poi.categories())
        if name.startswith("poi/"):
            db = poi.connect(poi.default_db(VAULT))
            if db is None:
                return self._json({"error": "no survival layers installed", "places": []}, 200)
            try:
                cat = (q.get("cat") or [None])[0] or None
                if name == "poi/stats":
                    # Good until the file changes: counting the world database takes seconds.
                    seen = os.stat(poi.default_db(VAULT))
                    return self._json(cached("poistats:%d:%d" % (seen.st_mtime_ns, seen.st_size), 86400,
                                             lambda: poi.stats(db)))
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

    def _body(self, limit=4 * 1024 * 1024):
        """The JSON a page sent. Only JSON is taken, so no other site can post here through a browser."""
        if "application/json" not in self.headers.get("Content-Type", ""):
            return None
        try:
            size = int(self.headers.get("Content-Length", "0"))
            return json.loads(self.rfile.read(size)) if 0 < size <= limit else None
        except ValueError:
            return None

    def _chat(self, ask):
        """Pass a conversation to the local model and hand its words back as they come."""
        where = _first_open(OLLAMA)
        if not where:
            return self._json({"error": "The assistant is not running. Start Genesis from the Start Genesis icon, "
                                        "or run: genesis start"}, 503)
        messages = [m for m in ask.get("messages", []) if m.get("role") in ("user", "assistant") and m.get("content")][-12:]
        system = ASSISTANT
        if ask.get("excerpts"):
            system += "\n\nExcerpts from the library on this box:\n" + "\n".join(
                "[%s] %s" % (str(e.get("book") or e.get("title"))[:120], str(e.get("text"))[:600]) for e in ask["excerpts"][:6])
        model = str(ask.get("model") or "")
        body = {"model": model, "stream": True, "messages": [{"role": "system", "content": system}] + messages}
        # Reasoning out loud before answering costs minutes on a small computer: only the model kept for hard problems does it.
        if not any(word in model for word, use in MODELS if use and use.startswith("hard")):
            body["think"] = False
        payload = json.dumps(body).encode("utf-8")
        conn = http.client.HTTPConnection(where[0], where[1], timeout=600)
        try:
            conn.request("POST", "/api/chat", payload, {"Content-Type": "application/json"})
            r = conn.getresponse()
            if r.status != 200:
                return self._json({"error": "The model server said: %s" % r.read(300).decode("utf-8", "replace")}, 502)
            self.send_response(200)
            self.send_header("Content-Type", "application/x-ndjson; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Transfer-Encoding", "chunked")
            self.end_headers()
            for line in r:
                self.wfile.write(b"%x\r\n%s\r\n" % (len(line), line))
                self.wfile.flush()
            self.wfile.write(b"0\r\n\r\n")
        except (BrokenPipeError, ConnectionResetError):
            pass  # the person closed the page or pressed stop
        finally:
            conn.close()
        return None

    def do_POST(self):
        path = unquote(urlsplit(self.path).path)
        try:
            data = self._body()
            if data is None:
                return self._json({"error": "send JSON"}, 400)
            if path == "/api/ai/chat":
                return self._chat(data)
            if path.startswith("/api/notes/"):
                note = path[len("/api/notes/"):]
                if not NOTE_NAME.fullmatch(note) or not isinstance(data.get("text"), str):
                    return self._json({"error": "a note needs a plain name ending in .md or .txt, and its text"}, 400)
                try:
                    os.makedirs(NOTES, exist_ok=True)
                    with open(os.path.join(NOTES, note + ".tmp"), "w", encoding="utf-8", newline="\n") as f:
                        f.write(data["text"])
                    os.replace(os.path.join(NOTES, note + ".tmp"), os.path.join(NOTES, note))
                except PermissionError:
                    return self._json({"error": "this user may not write to the drive. In a terminal, once: "
                                                "sudo chown -R $USER \"%s\"" % os.path.dirname(VAULT)}, 403)
                return self._json({"saved": note})
            return self._json({"error": "unknown endpoint"}, 404)
        except (BrokenPipeError, ConnectionResetError):
            return None
        except Exception as e:  # never take the server down for one bad request
            return self._json({"error": "%s: %s" % (type(e).__name__, e)}, 500)


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
