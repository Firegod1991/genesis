"""Vault operations: plan, fetch, verify, status."""

import datetime
import gzip
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tarfile
import threading
import time
import urllib.request
import zipfile
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlsplit

from . import catalog
from .util import ROOT, USER_AGENT, FetchError, Net, file_hash, http_open, human, log, read_json, write_json

STACK_OLLAMA = "http://127.0.0.1:11434"
# Model servers are on this machine or this network: never send those requests through a proxy.
_DIRECT = urllib.request.build_opener(urllib.request.ProxyHandler({}))
# One download at a time can draw a progress bar. Several at once would only scribble over each other.
PROGRESS = True
KIWIX = "https://download.kiwix.org/"


class _Mirrors(dict):
    """For this run: the mirrors that carry each Kiwix folder, the best speed seen from each host,
    and how many downloads each host is serving right now."""

    def __init__(self):
        super().__init__()
        self.hosts, self.busy = {}, {}

    def clear(self):
        super().clear()
        self.hosts.clear()
        self.busy.clear()


_mirror, _mirror_lock = _Mirrors(), threading.Lock()


def vault_path(args=None):
    path = (getattr(args, "vault", None) or os.environ.get("GENESIS_VAULT")
            or os.path.join(ROOT, "vault"))
    return os.path.abspath(os.path.expanduser(path))


def _meta(vault, name):
    return os.path.join(vault, ".genesis", name)


def _rel(path, vault):
    """A path inside the vault as the record keeps it: forward slashes on every system, so a drive
    filled on Windows reads the same on a Raspberry Pi."""
    return os.path.relpath(path, vault).replace(os.sep, "/")


def _inside(vault, rel):
    return os.path.join(vault, *rel.replace("\\", "/").split("/"))


def _state(vault):
    """What is installed. Records written by an older Genesis on Windows are read with their slashes turned."""
    state = read_json(_meta(vault, "state.json"), {})
    for st in state.values():
        st["files"] = [f.replace("\\", "/") for f in st.get("files", [])]
    return state


def load_config(vault):
    cfg = {"tier": "seed", "langs": ["eng"], "platforms": [catalog.host_platform(), "android"], "packs": None,
           "also": []}
    cfg.update(read_json(_meta(vault, "config.json"), {}))
    return cfg


def _selection(args, vault, save=False):
    cfg = load_config(vault)
    if getattr(args, "tier", None):
        cfg["tier"] = args.tier
    if getattr(args, "lang", None):
        cfg["langs"] = [x.strip() for x in args.lang.split(",") if x.strip()]
    if getattr(args, "platform", None):
        cfg["platforms"] = [x.strip() for x in args.platform.split(",") if x.strip()]
    ids = getattr(args, "id", None)
    packs = getattr(args, "pack", None)
    whole = not ids and not packs and not getattr(args, "tag", None)
    if getattr(args, "also", None):
        cfg["also"] = sorted(set(cfg.get("also") or []) | set(args.also))
    if save and whole:
        write_json(_meta(vault, "config.json"), cfg)  # remembered for `genesis update`
    sel = catalog.select(tier=cfg["tier"], packs=packs or cfg.get("packs"), ids=ids, langs=cfg["langs"],
                         platforms=cfg["platforms"], tags=getattr(args, "tag", None))
    named = set(ids or [])
    if whole:  # items wanted on top of the tier, such as the planet map beside ark
        extra = set(cfg.get("also") or []) - {i for _, i, _, _ in sel}
        if extra:
            sel += catalog.select(ids=sorted(extra), langs=cfg["langs"], platforms=cfg["platforms"])
            named |= extra
    return cfg, sel + _resolve_now(named - {i for _, i, _, _ in sel}, cfg["platforms"])


def _resolve_now(missing, platforms):
    """Ids asked for by name that the lock files lack (new since the last refresh): ask the source directly."""
    if not missing:
        return []
    from .refresh import _entry
    from .resolvers import ResolveError, resolve
    net, out = Net(), []
    for name in catalog.pack_names():
        for item in catalog.load_pack(name)["items"]:
            if item["id"] in missing:
                try:
                    for concrete, res in resolve(item, net):
                        res.pop("warnings", None)
                        entry = _entry(concrete, res)
                        out.append((name, concrete["id"], entry, catalog.wanted_files(entry, platforms)))
                except ResolveError as e:
                    log("cannot resolve %s right now: %s" % (item["id"], e))
    return out


def _free(path):
    while not os.path.exists(path):
        path = os.path.dirname(path)
    return shutil.disk_usage(path).free


def plan(args):
    vault = vault_path(args)
    cfg, sel = _selection(args, vault)
    state = _state(vault)
    per, need, unsized = {}, 0, 0
    for pack, iid, entry, files in sel:
        size = catalog.entry_size(entry, files)
        have = state.get(iid, {}).get("version") == entry.get("version")
        p = per.setdefault(pack, [0, 0, 0])
        p[0] += 1
        p[1] += size or 0
        if not have:
            p[2] += size or 0
            need += size or 0
            unsized += 0 if size else 1
    print("tier %s, languages %s, platforms %s" % (cfg["tier"], ",".join(cfg["langs"]), ",".join(cfg["platforms"])))
    print("%-12s %7s %12s %14s" % ("pack", "items", "total", "to download"))
    for pack in sorted(per):
        n, total, todo = per[pack]
        print("%-12s %7d %12s %14s" % (pack, n, human(total), human(todo)))
    free = _free(vault)
    print("\nto download: %s (plus %d items with no published size)" % (human(need), unsized))
    print("free space at %s: %s" % (vault, human(free)))
    if need > free * 0.95:
        print("NOT ENOUGH SPACE. Pick a smaller tier, fewer packs, or a bigger drive.")
        return 2
    return 0


def _folder(vault, iid, entry):
    """Where an item's downloads land."""
    sub = entry.get("dest", "misc")
    if entry.get("kind") == "bible":
        sub = os.path.join(sub, entry.get("meta", {}).get("translation", iid))
    return os.path.join(vault, sub)


def _retire(vault, state, iid):
    """Take an item out of the vault once a newer volume carries its contents."""
    for rel in state.pop(iid, {}).get("files", []):
        path = _inside(vault, rel)
        if os.path.isfile(path):
            os.remove(path)
            log("  removed %s: a newer volume on this drive replaces it" % rel)


def _tidy(vault, sel, state):
    """Clear out what the catalog has moved on from, before anything is downloaded.

    Partial downloads of files the catalog no longer lists can never be finished, so they go. Only
    folders that hold plain downloads are looked at, and only their own `.part` files: a folder that
    archives are unpacked into is left alone, whatever its contents are called. Items that a combined
    volume already on the drive has replaced go too.
    """
    plain, unpacked, waiting = set(), set(), set()
    known = [(iid, entry, entry.get("files", [])) for _, iid, entry in catalog.all_locked()]
    for iid, entry, files in known + [(iid, entry, files) for _, iid, entry, files in sel]:
        folder = _folder(vault, iid, entry)
        (unpacked if entry.get("post") in ("unzip", "untar") else plain).add(folder)
        waiting.update(os.path.join(folder, f["filename"] + ".part") for f in files)
    for folder in sorted(plain - unpacked):
        for name in sorted(os.listdir(folder)) if os.path.isdir(folder) else []:
            path = os.path.join(folder, name)
            if name.endswith(".part") and path not in waiting and os.path.isfile(path):
                log("removed %s (%s): a partial download of a file the catalog no longer lists"
                    % (_rel(path, vault), human(os.path.getsize(path))))
                os.remove(path)
    left = [old for _, iid, entry, _ in sel if state.get(iid, {}).get("version") == entry.get("version")
            for old in entry.get("replaces") or [] if old in state]
    for old in left:
        _retire(vault, state, old)
    if left:
        write_json(_meta(vault, "state.json"), state)


# ------------------------------------------------------------------ download and verify

def _speed(url, seconds=3.0):
    """Bytes a second from the start of a file. Nothing at all when the mirror will not answer."""
    start, got = time.time(), 0
    try:
        with http_open(url, headers={"Range": "bytes=0-67108863"}, retries=1, timeout=6) as r:
            start = time.time()  # the transfer is what is timed, not how long the mirror took to pick up
            while time.time() - start < seconds:
                block = r.read(256 * 1024)
                if not block:
                    break
                got += len(block)
    except (FetchError, OSError):
        return 0
    return got / max(time.time() - start, 0.001)


def _pick_mirror(url):
    """Time every mirror that carries this file for a few seconds. Returns the folder each one keeps
    it in, with the host, and notes how quick each host was."""
    name = url.rsplit("/", 1)[1]
    try:
        with http_open(url + ".meta4", retries=2, timeout=20) as r:
            listed = re.findall(r"<url[^>]*>\s*(https://[^<\s]+)\s*</url>", r.read().decode("utf-8", "replace"))
    except (FetchError, OSError):
        return []
    listed = [u for u in listed if u.endswith("/" + name)]
    if len(listed) < 2:
        return []
    with ThreadPoolExecutor(max_workers=len(listed)) as pool:
        speeds = list(pool.map(_speed, listed))
    found = []
    for u, speed in zip(listed, speeds):
        host = urlsplit(u).hostname
        # A line that is busy with other downloads measures low, so the best reading of a host counts.
        _mirror.hosts[host] = max(_mirror.hosts.get(host, 0), speed)
        found.append((u.rsplit("/", 1)[0], host))
    return found


def _kiwix_mirror(url, size, tried=()):
    """The same file on a quick Kiwix mirror, or None to take the one the publisher hands out.

    The publisher's own address sends everyone to a nearby mirror, and near is not always quick: forty
    times slower than the best one has been measured. A host that was quick for one folder is taken
    for the next folder it carries, and when several mirrors are within half the speed of the best,
    downloads are shared between them. Every download is still checked against the checksum the
    publisher lists, so a mirror can be slow or missing a file but never wrong.
    """
    if not url.startswith(KIWIX) or not size or size < 50e6:
        return None  # small files finish before a comparison would
    folder, name = url.rsplit("/", 1)
    with _mirror_lock:
        hosts, busy = _mirror.hosts, _mirror.busy
        if folder not in _mirror:
            _mirror[folder] = _pick_mirror(url)
            known = [(hosts[h], h) for _, h in _mirror[folder] if hosts.get(h)]
            if known:
                log("  mirror %s, %s a second" % (max(known)[1], human(max(known)[0])))
        live = [(m, h) for m, h in _mirror[folder] if hosts.get(h)]
        if not live:
            return None
        best = max(hosts[h] for _, h in live)
        quick = [(m, h) for m, h in live if hosts[h] * 2 >= best and h not in tried]
        if not quick:
            return None
        m, h = min(quick, key=lambda c: (busy.get(c[1], 0), -hosts[c[1]]))
        busy[h] = busy.get(h, 0) + 1
        return m + "/" + name


def _transfer(url, part):
    if shutil.which("curl"):
        # A link that stalls is dropped after two minutes below 1 KB/s and resumed, not waited on forever.
        cmd = ["curl", "-fL", "--retry", "5", "--retry-delay", "5", "--connect-timeout", "30",
               "--speed-limit", "1024", "--speed-time", "120", "-C", "-", "-A", USER_AGENT, "-o", part, url]
        cmd.insert(1, "--progress-bar" if PROGRESS and sys.stderr.isatty() else "-sS")
        rc = subprocess.call(cmd)
        if rc == 33:  # server cannot resume: start over once
            os.remove(part)
            rc = subprocess.call(cmd)
        if rc != 0:
            raise FetchError("curl exit %d for %s" % (rc, url))
    else:
        start = os.path.getsize(part) if os.path.exists(part) else 0
        headers = {"Range": "bytes=%d-" % start} if start else None
        with http_open(url, headers=headers, timeout=120) as r:
            mode = "ab" if start and r.status == 206 else "wb"
            with open(part, mode) as f:
                shutil.copyfileobj(r, f, 1024 * 1024)


def _download(url, dest, size=None):
    part = dest + ".part"
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    if size and os.path.exists(part) and os.path.getsize(part) == size:
        os.replace(part, dest)
        return
    tried = []
    while True:
        quick = _kiwix_mirror(url, size, tried)
        if not quick:
            break
        host = urlsplit(quick).hostname
        try:
            _transfer(quick, part)
            os.replace(part, dest)
            return
        except (FetchError, OSError) as e:
            tried.append(host)
            log("  %s gave up (%s): trying elsewhere" % (host, e))
        finally:
            with _mirror_lock:
                _mirror.busy[host] = max(0, _mirror.busy.get(host, 0) - 1)
    _transfer(url, part)
    os.replace(part, dest)


def _expected(f, net):
    """Return (algo, hex digest) for a file entry, fetching a sidecar checksum if needed."""
    for algo in ("sha256", "sha512", "md5"):
        if f.get(algo):
            return algo, f[algo].lower()
        if f.get(algo + "_url"):
            token = net.text(f[algo + "_url"]).split()[0].strip().lower()
            return algo, token
    return None, None


def _check(path, f, net):
    """Verify a downloaded file. Returns a short description of what was checked."""
    algo, want = _expected(f, net)
    if algo:
        got = file_hash(path, algo)
        if got != want:
            raise FetchError("%s mismatch for %s: expected %s, got %s" % (algo, os.path.basename(path), want, got))
        return algo
    if path.endswith(".zip"):
        with zipfile.ZipFile(path) as z:
            bad = z.testzip()
        if bad:
            raise FetchError("corrupt zip member %s in %s" % (bad, path))
        return "zip integrity"
    if f.get("size"):
        if os.path.getsize(path) != f["size"]:
            raise FetchError("size mismatch for %s" % path)
        return "size"
    return "none published"


def _safe_join(base, name):
    target = os.path.normpath(os.path.join(base, name))
    if not (target + os.sep).startswith(os.path.abspath(base) + os.sep):
        raise FetchError("archive member escapes its folder: %s" % name)
    return target


def _unzip(path, into):
    with zipfile.ZipFile(path) as z:
        for m in z.infolist():
            _safe_join(into, m.filename)
        z.extractall(into)


def _untar(path, into):
    """Extract a tarball, dropping its single top level folder."""
    os.makedirs(into, exist_ok=True)
    with tarfile.open(path) as t:
        for m in t:
            parts = m.name.split("/", 1)
            if len(parts) < 2 or not (m.isfile() or m.isdir()):
                continue
            target = _safe_join(into, parts[1])
            if m.isdir():
                os.makedirs(target, exist_ok=True)
            else:
                os.makedirs(os.path.dirname(target), exist_ok=True)
                with t.extractfile(m) as src, open(target, "wb") as dst:
                    shutil.copyfileobj(src, dst)


def _ollama_command():
    exe = shutil.which("ollama")
    return [exe, "serve"] if exe else None


def _stack_ollama_running():
    """True when the Ollama that `genesis up ai` starts is running. It keeps its models in the vault."""
    if not shutil.which("docker"):
        return False
    try:
        out = subprocess.run(["docker", "compose", "--project-name", "genesis", "ps", "--status", "running",
                              "--services"], capture_output=True, text=True, timeout=30).stdout
    except (OSError, subprocess.SubprocessError):
        return False
    return "ollama" in out.split()


class Models:
    """Pulls models so that they land inside the vault, whichever machine fills it.

    The stack keeps its models in <vault>/models/ollama. When the stack's own Ollama is running, it
    does the pulling. Otherwise an Ollama installed on this machine is started for the length of the
    fetch with that folder as its store, so nothing lands in the owner's own model library.
    GENESIS_OLLAMA names another server to pull through, wherever that one keeps its models.
    """

    def __init__(self, vault):
        self.vault, self.host, self.proc, self.why = vault, None, None, None
        self.lock = threading.Lock()

    def start(self):
        explicit = os.environ.get("GENESIS_OLLAMA")
        if explicit:
            return (explicit if explicit.startswith("http") else "http://" + explicit).rstrip("/")
        if _stack_ollama_running():
            return STACK_OLLAMA
        cmd = _ollama_command()
        if not cmd:
            self.why = "no Ollama to pull with. Start the stack with `genesis up ai` or install Ollama, then fetch again."
            return None
        store = os.path.join(self.vault, "models", "ollama", "models")
        os.makedirs(store, exist_ok=True)
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]
        host = "http://127.0.0.1:%d" % port
        env = dict(os.environ, OLLAMA_MODELS=store, OLLAMA_HOST="127.0.0.1:%d" % port)
        try:
            self.proc = subprocess.Popen(cmd, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                         creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except OSError as e:
            self.why = "Ollama would not start (%s)." % e
            return None
        for _ in range(60):
            try:
                with _DIRECT.open(host + "/api/version", timeout=2):
                    log("  models go into %s" % store)
                    return host
            except (OSError, ValueError):
                if self.proc.poll() is not None:
                    break
                time.sleep(0.5)
        self.close()
        self.why = "Ollama started and did not answer."
        return None

    def pull(self, model):
        with self.lock:
            if self.host is None and self.why is None:
                self.host = self.start()
        if self.host is None:
            log("  skipped: " + self.why)
            return False
        req = urllib.request.Request(self.host + "/api/pull", data=json.dumps({"model": model}).encode(),
                                     headers={"Content-Type": "application/json"})
        try:
            with _DIRECT.open(req, timeout=3600) as r:
                for line in r:
                    msg = json.loads(line or b"{}")
                    if msg.get("error"):
                        raise FetchError("ollama: " + msg["error"])
            return True
        except (OSError, ValueError) as e:
            log("  skipped: Ollama is not reachable at %s (%s)." % (self.host, e))
            return False

    def close(self):
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=15)
            except subprocess.TimeoutExpired:
                self.proc.kill()
        self.proc = None


def _source(entry, files):
    """What an item is downloaded from. No one source is given every slot while others wait."""
    if entry.get("kind") == "model":
        return "models"
    url = files[0]["url"] if files else ""
    if url.startswith(KIWIX):
        return url.rsplit("/", 1)[0]  # each Kiwix folder has its own set of mirrors
    return urlsplit(url).hostname or ""


def _fetch_item(vault, iid, entry, files, net, state, models):
    kind = entry.get("kind")
    if kind == "image":
        return None  # pulled by `genesis up` and saved by `genesis seal`
    if kind == "model":
        if not models.pull(entry["model"]):
            return None
        return {"version": entry.get("version"), "files": [], "model": entry["model"]}
    folder = _folder(vault, iid, entry)
    written = []
    for f in files:
        dest = os.path.join(folder, f["filename"])
        if not os.path.exists(dest) or (f.get("size") and os.path.getsize(dest) != f["size"]):
            log("  get %s (%s)" % (f["filename"], human(f.get("size"))))
            _download(f["url"], dest, f.get("size"))
        how = _check(dest, f, net)
        log("  ok %s (%s)" % (f["filename"], how))
        written.append(_rel(dest, vault))
        post = entry.get("post")
        if post == "unzip" and f.get("role", "html") == "html":
            _unzip(dest, folder)
            os.remove(dest)
            written[-1] = _rel(folder, vault)
        elif post == "untar":
            _untar(dest, folder)
            os.remove(dest)
            written[-1] = _rel(folder, vault)
        elif post == "gunzip":
            out = dest[:-3]
            with gzip.open(dest, "rb") as src, open(out + ".part", "wb") as dst:
                shutil.copyfileobj(src, dst, 1024 * 1024)
            os.replace(out + ".part", out)
            os.remove(dest)
            written[-1] = _rel(out, vault)
            if kind == "poi":
                # The dashboard mounts the vault read only and cannot keep its own count.
                from . import poi
                log("  counting places ...")
                db = poi.connect(out)
                log("  %s places" % format(poi.stats(db)["total"], ","))
                db.close()
    if kind == "bible":
        write_json(os.path.join(folder, "genesis.json"), dict(
            entry.get("meta", {}), id=iid, title=entry.get("title"), license=entry.get("license"),
            version=entry.get("version"), lang=entry.get("lang")))
    # A newer version replaced an older one: drop the superseded files Genesis put there.
    for old in state.get(iid, {}).get("files", []):
        if old not in written and os.path.isfile(_inside(vault, old)):
            os.remove(_inside(vault, old))
    return {"version": entry.get("version"), "files": written}


def fetch(args):
    vault = vault_path(args)
    cfg, sel = _selection(args, vault, save=True)
    state = _state(vault)
    _tidy(vault, sel, state)
    todo = [(p, i, e, f) for p, i, e, f in sel
            if state.get(i, {}).get("version") != e.get("version") and e.get("kind") != "image"]
    need = sum(catalog.entry_size(e, f) or 0 for _, _, e, f in todo)
    free = _free(vault)
    log("vault %s: %d items selected, %d to fetch, %s to download, %s free"
        % (vault, len(sel), len(todo), human(need), human(free)))
    if need > free * 0.95:
        log("NOT ENOUGH SPACE. Run `genesis plan` and pick a smaller tier or fewer packs.")
        return 2
    if need > 20e9 and not args.yes and sys.stdin.isatty():
        if input("Download %s? [y/N] " % human(need)).strip().lower() != "y":
            return 1
    global PROGRESS
    jobs = max(1, getattr(args, "jobs", None) or 4)
    PROGRESS = jobs == 1
    net, models, lock, failed, done = Net(), Models(vault), threading.Lock(), [], []
    # One slow source must not hold every slot: while items from elsewhere wait, a source gets half.
    waiting, busy, halt, share = list(enumerate(todo, 1)), {}, [], max(1, jobs // 2)

    def one(n, pack, iid, entry, files):
        log("[%d/%d] %s: %s" % (n, len(todo), pack, entry.get("title") or iid))
        try:
            result = _fetch_item(vault, iid, entry, files, net, state, models)
        except (FetchError, OSError, zipfile.BadZipFile, tarfile.TarError) as e:
            log("  FAILED %s: %s" % (iid, e))
            failed.append(iid)
            return
        if result:
            result["fetched"] = datetime.date.today().isoformat()
            result["pack"] = pack
            with lock:  # one writer at a time, so no item is lost from the record
                state[iid] = result
                for old in entry.get("replaces") or []:
                    _retire(vault, state, old)
                write_json(_meta(vault, "state.json"), state)
                done.append(iid)

    def take():
        with lock:
            if halt or not waiting:
                return None
            job = next((w for w in waiting if busy.get(_source(w[1][2], w[1][3]), 0) < share), waiting[0])
            waiting.remove(job)
            key = _source(job[1][2], job[1][3])
            busy[key] = busy.get(key, 0) + 1
            return job, key

    def work():
        while True:
            got = take()
            if got is None:
                return
            (n, item), key = got
            try:
                one(n, *item)
            except BaseException:
                halt.append(True)  # anything unexpected stops the run here, as it always has
                raise
            finally:
                with lock:
                    busy[key] -= 1
            with lock:
                if not args.keep_going and len(failed) >= 5 and not done and not halt:
                    log("five failures and no success: stopping. Check your connection.")
                    halt.append(True)

    pool = ThreadPoolExecutor(max_workers=jobs)
    try:
        for worker in [pool.submit(work) for _ in range(jobs)]:
            worker.result()
    finally:
        pool.shutdown(wait=True)
        models.close()
    log("fetched %d, failed %d%s" % (len(done), len(failed), (": " + ", ".join(sorted(failed)[:20])) if failed else ""))
    return 1 if failed else 0


def status(args):
    vault = vault_path(args)
    cfg, sel = _selection(args, vault)
    state = _state(vault)
    meta = read_json(os.path.join(catalog.LOCK_DIR, "_meta.json"), {})
    rows = {}
    for pack, iid, entry, _ in sel:
        if entry.get("kind") == "image":
            continue
        r = rows.setdefault(pack, {"current": 0, "outdated": 0, "missing": 0})
        have = state.get(iid)
        r["missing" if not have else "current" if have.get("version") == entry.get("version") else "outdated"] += 1
    print("vault: %s\ncatalog refreshed: %s\ntier: %s  languages: %s" % (
        vault, meta.get("refreshed", "never"), cfg["tier"], ",".join(cfg["langs"])))
    print("%-12s %8s %9s %8s" % ("pack", "current", "outdated", "missing"))
    for pack in sorted(rows):
        r = rows[pack]
        print("%-12s %8d %9d %8d" % (pack, r["current"], r["outdated"], r["missing"]))
    return 0


def verify(args):
    """Re check every installed file against the catalog checksum for its installed version."""
    vault = vault_path(args)
    state = _state(vault)
    locked = {iid: entry for _, iid, entry in catalog.all_locked()}
    net, bad, work = Net(), [], []
    for iid, st in sorted(state.items()):
        entry = locked.get(iid, {})
        by_name = {f["filename"]: f for f in entry.get("files", [])} if entry.get("version") == st.get("version") else {}
        for rel in st.get("files", []):
            path = _inside(vault, rel)
            if not os.path.exists(path):
                bad.append("%s: missing %s" % (iid, rel))
                continue
            f = by_name.get(rel.rsplit("/", 1)[-1])
            if f and os.path.isfile(path):
                work.append((iid, path, f))

    def one(job):
        iid, path, f = job
        try:
            _expected(f, net)  # some publishers keep the checksum in a file beside the download
        except FetchError:
            return None  # no connection, or the publisher moved on: nothing to compare with
        try:
            _check(path, f, net)
        except FetchError as e:
            return "%s: %s" % (iid, e)
        return True

    with ThreadPoolExecutor(max_workers=max(1, getattr(args, "jobs", None) or 4)) as pool:
        results = list(pool.map(one, work))
    bad += [r for r in results if isinstance(r, str)]
    for b in bad:
        print("BAD " + b)
    print("%d installed items, %d files re hashed, %d problems" % (len(state), results.count(True), len(bad)))
    if None in results:
        print("%d files had nothing to be compared with: their checksums are published online and could not be read.\n"
              "`genesis verify --sealed` checks every file against the seal and needs no connection." % results.count(None))
    return 1 if bad else 0
