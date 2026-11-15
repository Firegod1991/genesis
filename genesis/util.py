"""Shared helpers: HTTP, hashing, formatting. Standard library only."""

import hashlib
import json
import os
import re
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

from . import __version__

# Deliberately generic: no account names, no contact details.
USER_AGENT = "GenesisCatalog/" + __version__
TIMEOUT = 60
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class FetchError(Exception):
    """A network request failed after retries."""


def log(msg):
    print(msg, file=sys.stderr, flush=True)


def _headers(url, extra=None):
    h = {"User-Agent": USER_AGENT, "Accept": "*/*"}
    host = urllib.parse.urlsplit(url).hostname or ""
    token = os.environ.get("GENESIS_GITHUB_TOKEN") or os.environ.get("GITHUB_TOKEN")
    if token and host == "api.github.com":
        h["Authorization"] = "Bearer " + token
        h["X-GitHub-Api-Version"] = "2022-11-28"
    if extra:
        h.update(extra)
    return h


def http_open(url, method="GET", headers=None, retries=3, timeout=TIMEOUT):
    """Open a URL with retries. Returns the response object (caller closes)."""
    last = None
    for attempt in range(retries):
        req = urllib.request.Request(url, method=method, headers=_headers(url, headers))
        try:
            return urllib.request.urlopen(req, timeout=timeout)
        except urllib.error.HTTPError as e:
            last = e
            # Client errors will not fix themselves; rate limits and server errors might.
            if e.code not in (408, 425, 429, 500, 502, 503, 504):
                raise FetchError("HTTP %s for %s" % (e.code, url)) from e
        except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as e:
            last = e
        time.sleep(1.5 * (attempt + 1))
    raise FetchError("%s for %s" % (last, url))


class Net:
    """Per run HTTP client with an in memory cache so shared indexes load once."""

    def __init__(self):
        self._cache = {}
        self._lock = threading.Lock()
        self._inflight = {}

    def get(self, url, headers=None):
        key = (url, json.dumps(headers, sort_keys=True) if headers else "")
        with self._lock:
            if key in self._cache:
                return self._cache[key]
            gate = self._inflight.setdefault(key, threading.Lock())
        with gate:
            with self._lock:
                if key in self._cache:
                    return self._cache[key]
            with http_open(url, headers=headers) as r:
                data = r.read()
            with self._lock:
                self._cache[key] = data
            return data

    def text(self, url, headers=None):
        return self.get(url, headers).decode("utf-8", "replace")

    def json(self, url, headers=None):
        return json.loads(self.get(url, headers))

    def head(self, url, headers=None):
        """Return (final_url, headers dict with lower case keys)."""
        with http_open(url, method="HEAD", headers=headers) as r:
            return r.geturl(), {k.lower(): v for k, v in r.headers.items()}


def vkey(text):
    """Sort key that orders version like strings naturally: 9.2 < 9.10 < 10.0."""
    return [int(x) for x in re.findall(r"\d+", text)]


def fkey(name):
    """Order file names by the version inside them, so 26.04.1 beats 26.04 despite `amd64`."""
    m = re.search(r"\d+(?:[._]\d+)+|\d{6,8}", name)
    return (vkey(m.group(0)) if m else [], vkey(name))


def human(n):
    if n is None:
        return "?"
    n = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1000 or unit == "TB":
            return ("%.0f %s" if unit in ("B", "KB") else "%.1f %s") % (n, unit)
        n /= 1000.0
    return "?"


def file_hash(path, algo="sha256", chunk=4 * 1024 * 1024):
    h = hashlib.new(algo)
    with open(path, "rb") as f:
        while True:
            block = f.read(chunk)
            if not block:
                break
            h.update(block)
    return h.hexdigest()


def read_json(path, default=None):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return default


def write_json(path, data):
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=1, ensure_ascii=False, sort_keys=True)
        f.write("\n")
    os.replace(tmp, path)


def parse_checksums(text):
    """Parse GNU (`hash  file`, `hash *file`) and BSD (`ALGO (file) = hash`) lists."""
    out = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        m = re.match(r"^[A-Za-z0-9]+ \((.+)\) = ([0-9a-fA-F]{32,128})$", line)
        if m:
            out.append((m.group(2).lower(), m.group(1)))
            continue
        m = re.match(r"^([0-9a-fA-F]{32,128})\s+\*?(.+)$", line)
        if m:
            out.append((m.group(1).lower(), m.group(2).strip()))
    return out
