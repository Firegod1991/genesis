"""Vault operations: plan, fetch, verify, status."""

import datetime
import gzip
import json
import os
import shutil
import subprocess
import sys
import tarfile
import urllib.request
import zipfile

from . import catalog
from .util import ROOT, USER_AGENT, FetchError, Net, file_hash, http_open, human, log, read_json, write_json


def vault_path(args=None):
    path = (getattr(args, "vault", None) or os.environ.get("GENESIS_VAULT")
            or os.path.join(ROOT, "vault"))
    return os.path.abspath(os.path.expanduser(path))


def _meta(vault, name):
    return os.path.join(vault, ".genesis", name)


def load_config(vault):
    cfg = {"tier": "seed", "langs": ["eng"], "platforms": [catalog.host_platform(), "android"], "packs": None}
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
    if save and not ids and not packs and not getattr(args, "tag", None):
        write_json(_meta(vault, "config.json"), cfg)  # remembered for `genesis update`
    sel = catalog.select(tier=cfg["tier"], packs=packs or cfg.get("packs"), ids=ids, langs=cfg["langs"],
                         platforms=cfg["platforms"], tags=getattr(args, "tag", None))
    return cfg, sel + _resolve_now(set(ids or []) - {i for _, i, _, _ in sel}, cfg["platforms"])


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
    state = read_json(_meta(vault, "state.json"), {})
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


# ------------------------------------------------------------------ download and verify

def _download(url, dest, size=None):
    part = dest + ".part"
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    if size and os.path.exists(part) and os.path.getsize(part) == size:
        os.replace(part, dest)
        return
    if shutil.which("curl"):
        cmd = ["curl", "-fL", "--retry", "5", "--retry-delay", "5", "--connect-timeout", "30", "-C", "-",
               "-A", USER_AGENT, "-o", part, url]
        cmd.insert(1, "--progress-bar" if sys.stderr.isatty() else "-sS")
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


def _ollama_pull(model):
    host = os.environ.get("OLLAMA_HOST", "http://127.0.0.1:11434").rstrip("/")
    if not host.startswith("http"):
        host = "http://" + host
    req = urllib.request.Request(host + "/api/pull", data=json.dumps({"model": model}).encode(),
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=3600) as r:
            for line in r:
                msg = json.loads(line or b"{}")
                if msg.get("error"):
                    raise FetchError("ollama: " + msg["error"])
        return True
    except (OSError, ValueError) as e:
        log("  skipped: Ollama is not reachable at %s (%s). Start it with `genesis up ai`, then fetch again." % (host, e))
        return False


def _fetch_item(vault, iid, entry, files, net, state):
    kind = entry.get("kind")
    if kind == "image":
        return None  # pulled by `genesis up` and saved by `genesis seal`
    if kind == "model":
        if not _ollama_pull(entry["model"]):
            return None
        return {"version": entry.get("version"), "files": [], "model": entry["model"]}
    sub = entry.get("dest", "misc")
    if kind == "bible":
        sub = os.path.join(sub, entry.get("meta", {}).get("translation", iid))
    folder = os.path.join(vault, sub)
    written = []
    for f in files:
        dest = os.path.join(folder, f["filename"])
        if not os.path.exists(dest) or (f.get("size") and os.path.getsize(dest) != f["size"]):
            log("  get %s (%s)" % (f["filename"], human(f.get("size"))))
            _download(f["url"], dest, f.get("size"))
        how = _check(dest, f, net)
        log("  ok %s (%s)" % (f["filename"], how))
        written.append(os.path.relpath(dest, vault))
        post = entry.get("post")
        if post == "unzip" and f.get("role", "html") == "html":
            _unzip(dest, folder)
            os.remove(dest)
            written[-1] = os.path.relpath(folder, vault)
        elif post == "untar":
            _untar(dest, folder)
            os.remove(dest)
            written[-1] = os.path.relpath(folder, vault)
        elif post == "gunzip":
            out = dest[:-3]
            with gzip.open(dest, "rb") as src, open(out + ".part", "wb") as dst:
                shutil.copyfileobj(src, dst, 1024 * 1024)
            os.replace(out + ".part", out)
            os.remove(dest)
            written[-1] = os.path.relpath(out, vault)
    if kind == "bible":
        write_json(os.path.join(folder, "genesis.json"), dict(
            entry.get("meta", {}), id=iid, title=entry.get("title"), license=entry.get("license"),
            version=entry.get("version"), lang=entry.get("lang")))
    # A newer version replaced an older one: drop the superseded files Genesis put there.
    for old in state.get(iid, {}).get("files", []):
        if old not in written and os.path.isfile(os.path.join(vault, old)):
            os.remove(os.path.join(vault, old))
    return {"version": entry.get("version"), "files": written}


def fetch(args):
    vault = vault_path(args)
    cfg, sel = _selection(args, vault, save=True)
    state = read_json(_meta(vault, "state.json"), {})
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
    net, failed, done = Net(), [], 0
    for n, (pack, iid, entry, files) in enumerate(todo, 1):
        log("[%d/%d] %s: %s" % (n, len(todo), pack, entry.get("title") or iid))
        try:
            result = _fetch_item(vault, iid, entry, files, net, state)
        except (FetchError, OSError, zipfile.BadZipFile, tarfile.TarError) as e:
            log("  FAILED %s" % e)
            failed.append(iid)
            if not args.keep_going and len(failed) >= 5 and done == 0:
                log("five failures and no success: stopping. Check your connection.")
                break
            continue
        if result:
            result["fetched"] = datetime.date.today().isoformat()
            result["pack"] = pack
            state[iid] = result
            write_json(_meta(vault, "state.json"), state)
            done += 1
    log("fetched %d, failed %d%s" % (done, len(failed), (": " + ", ".join(failed[:20])) if failed else ""))
    return 1 if failed else 0


def status(args):
    vault = vault_path(args)
    cfg, sel = _selection(args, vault)
    state = read_json(_meta(vault, "state.json"), {})
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
    state = read_json(_meta(vault, "state.json"), {})
    locked = {iid: entry for _, iid, entry in catalog.all_locked()}
    net, bad, checked = Net(), [], 0
    for iid, st in sorted(state.items()):
        entry = locked.get(iid, {})
        by_name = {f["filename"]: f for f in entry.get("files", [])} if entry.get("version") == st.get("version") else {}
        for rel in st.get("files", []):
            path = os.path.join(vault, rel)
            if not os.path.exists(path):
                bad.append("%s: missing %s" % (iid, rel))
                continue
            f = by_name.get(os.path.basename(rel))
            if f and os.path.isfile(path):
                try:
                    _check(path, f, net)
                    checked += 1
                except FetchError as e:
                    bad.append("%s: %s" % (iid, e))
    for b in bad:
        print("BAD " + b)
    print("%d installed items, %d files re hashed, %d problems" % (len(state), checked, len(bad)))
    return 1 if bad else 0
