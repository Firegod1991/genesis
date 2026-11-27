"""Choose what to download from the dashboard, and watch it come down.

The dashboard lists the shelves with what each costs on this drive, a person ticks what they want, and a
fetch runs on the box in the background, the same `genesis fetch` a terminal runs. Only someone at the box
itself may start or stop one: a phone on the hotspot can watch, and cannot fill the drive.
"""

import json
import os
import signal
import subprocess
import sys
import time

from . import catalog
from .util import ROOT, human, read_json, write_json

# The order shelves are offered in: what helps first, then the large libraries.
ORDER = ("survival", "medical", "maps", "scripture", "ai", "knowledge", "library", "radio", "tools", "apps",
         "code", "isos")
GET = "get"  # the folder in <vault>/.genesis that holds the download now running


def _folder(vault):
    return os.path.join(vault, ".genesis", GET)


def _installed(vault):
    from .fetch import _state
    return _state(vault)


def _wanted(vault):
    """Everything this drive could carry, for its languages and machines: (pack, id, entry, files)."""
    from .fetch import _carried, load_config
    cfg = load_config(vault)
    every = catalog.select(tier="eden", langs=cfg["langs"], platforms=cfg["platforms"])
    return cfg, [w for w in every if _carried(w[2], w[3])]


def _cost(items, state):
    total = todo = n = unsized = 0
    for _, iid, entry, files in items:
        size = catalog.entry_size(entry, files) or 0
        total += size
        if state.get(iid, {}).get("version") != entry.get("version"):
            todo += size
            n += 1
            unsized += not size
    return {"bytes": total, "to_get": todo, "items": n, "of": len(items), "unsized": unsized}


def assistant_ids():
    from .box import assistant_for
    from .util import memory
    model, name = assistant_for(memory()[0])
    return ["ollama_runtime", model], name


def options(vault):
    """The shelves, what each tier of each costs here, and what is already on the drive."""
    from .fetch import _free
    cfg, every = _wanted(vault)
    state = _installed(vault)
    shelves = []
    names = sorted({p for p, _, _, _ in every}, key=lambda p: (ORDER.index(p) if p in ORDER else len(ORDER), p))
    for name in names:
        pack = catalog.load_pack(name)
        mine = [w for w in every if w[0] == name]
        tiers = {t: _cost([w for w in mine if catalog.tier_includes(t, w[2].get("tier", "eden"))], state)
                 for t in catalog.TIERS}
        shelves.append({"pack": name, "title": pack.get("title") or name, "about": pack.get("about") or "",
                        "tiers": tiers})
    starter = [w for w in catalog.select(tier=catalog.STARTER, langs=cfg["langs"], platforms=cfg["platforms"])]
    ids, name = assistant_ids()
    helper = [w for w in every if w[1] in ids]
    return {"shelves": shelves, "starter": _cost(starter, state),
            "assistant": dict(_cost(helper, state), name=name), "free": _free(vault),
            "langs": cfg["langs"], "platforms": cfg["platforms"]}


def chosen(vault, choices):
    """The items to bring down for what was ticked, leaving out what is already here."""
    cfg, every = _wanted(vault)
    state = _installed(vault)
    ids = set()
    for c in choices:
        if c.get("set") == "starter":
            ids |= {i for _, i, _, _ in catalog.select(tier=catalog.STARTER, langs=cfg["langs"], platforms=cfg["platforms"])}
        elif c.get("set") == "assistant":
            ids |= set(assistant_ids()[0])
        elif c.get("pack") and c.get("tier") in catalog.TIERS:
            ids |= {i for p, i, e, _ in every if p == c["pack"] and catalog.tier_includes(c["tier"], e.get("tier", "eden"))}
        else:
            raise ValueError("a choice is a pack and a tier, or the starter, or the assistant")
    return [w for w in every if w[1] in ids and state.get(w[1], {}).get("version") != w[2].get("version")]


def cost(vault, choices):
    """What a set of ticks would bring down: bytes, items, and items whose size is not published."""
    todo = chosen(vault, choices)
    sizes = [catalog.entry_size(e, f) or 0 for _, _, e, f in todo]
    return {"to_get": sum(sizes), "items": len(todo), "unsized": sum(1 for x in sizes if not x)}


def alive(pid):
    if not pid:
        return False
    if os.name == "nt":
        import ctypes
        handle = ctypes.windll.kernel32.OpenProcess(0x1000 | 0x00100000, False, int(pid))  # query, synchronize
        if not handle:
            return False
        try:
            return ctypes.windll.kernel32.WaitForSingleObject(handle, 0) == 0x102  # still running
        finally:
            ctypes.windll.kernel32.CloseHandle(handle)
    try:
        os.kill(int(pid), 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    try:  # a child that has ended and not been waited for is gone too
        done, _ = os.waitpid(int(pid), os.WNOHANG)
        return done == 0
    except ChildProcessError:
        return True


def start(vault, choices, python=None):
    """Start a fetch of what was ticked, in the background. Returns the job, or raises ValueError."""
    from .fetch import _folder as item_folder, _free, load_config, _meta
    job = read_json(os.path.join(_folder(vault), "job.json"), {})
    if job and alive(job.get("pid")) and not os.path.exists(os.path.join(_folder(vault), "done.json")):
        raise ValueError("a download is already running")
    todo = chosen(vault, choices)
    if not todo:
        raise ValueError("everything ticked is already on the drive")
    need = sum(catalog.entry_size(e, f) or 0 for _, _, e, f in todo)
    if need > _free(vault) * 0.95:
        raise ValueError("that needs %s and the drive has %s free" % (human(need), human(_free(vault))))
    folder = _folder(vault)
    os.makedirs(folder, exist_ok=True)
    for name in ("done.json", "log.txt"):
        if os.path.exists(os.path.join(folder, name)):
            os.remove(os.path.join(folder, name))
    with open(os.path.join(folder, "ids.txt"), "w", encoding="utf-8") as f:
        f.write("\n".join(iid for _, iid, _, _ in todo) + "\n")
    items = [{"id": iid, "title": entry.get("title") or iid, "version": entry.get("version"),
              "bytes": catalog.entry_size(entry, files) or 0,
              "parts": [os.path.join(item_folder(vault, iid, entry), fl["filename"] + ".part") for fl in files]}
             for _, iid, entry, files in todo]
    # Kept on top of the tier, so `genesis update` brings their new versions too.
    cfg = load_config(vault)
    cfg["also"] = sorted(set(cfg.get("also") or []) | {i["id"] for i in items})
    write_json(_meta(vault, "config.json"), cfg)
    cmd = [python or sys.executable, "-m", "genesis.picker", "run", vault]
    log = open(os.path.join(folder, "log.txt"), "ab")
    kw = ({"creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)}
          if os.name == "nt" else {"start_new_session": True})
    env = dict(os.environ, PYTHONUNBUFFERED="1")
    proc = subprocess.Popen(cmd, cwd=ROOT, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT, env=env, **kw)
    log.close()
    job = {"pid": proc.pid, "started": time.time(), "choices": choices, "items": items, "bytes": need}
    write_json(os.path.join(folder, "job.json"), job)
    return status(vault)


def _tail(path, lines=12):
    try:
        with open(path, "rb") as f:
            f.seek(0, 2)
            f.seek(max(0, f.tell() - 16384))
            text = f.read().decode("utf-8", "replace")
    except OSError:
        return []
    return [ln for ln in text.replace("\r", "\n").split("\n") if ln.strip()][-lines:]


def status(vault):
    """Where the download stands: items done, bytes so far, the last lines it wrote, and whether it runs."""
    folder = _folder(vault)
    job = read_json(os.path.join(folder, "job.json"), {})
    if not job:
        return {"running": False, "job": False}
    done = read_json(os.path.join(folder, "done.json"))
    state = _installed(vault)
    finished, so_far, now = [], 0, []
    for item in job.get("items", []):
        if state.get(item["id"], {}).get("version") == item.get("version"):
            finished.append(item["id"])
            so_far += item["bytes"]
            continue
        part = sum(os.path.getsize(p) for p in item["parts"] if os.path.isfile(p))
        if part:
            so_far += min(part, item["bytes"] or part)
            now.append(item["title"])
    blobs = os.path.join(vault, "models", "ollama", "models", "blobs")
    if os.path.isdir(blobs):  # a model coming down straight from the registry
        so_far += sum(os.path.getsize(os.path.join(blobs, n)) for n in os.listdir(blobs) if n.endswith(".part"))
    running = done is None and alive(job.get("pid"))
    return {"running": running, "job": True, "started": job.get("started"), "items": len(job.get("items", [])),
            "done": len(finished), "bytes": job.get("bytes", 0), "so_far": min(so_far, job.get("bytes", 0) or so_far),
            "now": now[:4], "log": _tail(os.path.join(folder, "log.txt")),
            "result": done, "stopped": not running and done is None}


def stop(vault):
    job = read_json(os.path.join(_folder(vault), "job.json"), {})
    pid = job.get("pid")
    if not alive(pid):
        return status(vault)
    if os.name == "nt":  # the fetch and the downloads it started, the whole tree
        subprocess.call(["taskkill", "/PID", str(int(pid)), "/T", "/F"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    else:
        try:
            os.killpg(int(pid), signal.SIGTERM)
        except (ProcessLookupError, PermissionError):
            pass
        for _ in range(20):
            if not alive(pid):
                break
            time.sleep(0.25)
    return status(vault)


def run(vault):
    """The download itself, as the dashboard starts it: a fetch of the ids it wrote down."""
    import argparse
    from .fetch import fetch
    folder = _folder(vault)
    args = argparse.Namespace(vault=vault, tier=None, pack=None, tag=None, lang=None, platform=None, also=None,
                              id=None, ids_from=os.path.join(folder, "ids.txt"), yes=True, keep_going=True, jobs=4)
    code = 1
    try:
        code = fetch(args)
        try:
            from .box import ready
            ready(argparse.Namespace(vault=vault))  # new programs unpacked, new books listed for the reader
        except Exception as e:  # what came down stays down: making it ready again is for the next start
            print("could not make the new downloads ready: %s" % e, flush=True)
    finally:
        write_json(os.path.join(folder, "done.json"), {"code": code, "ended": time.time()})
    return code


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "run":
        sys.exit(run(sys.argv[2]))
    print(json.dumps(status(sys.argv[1]) if len(sys.argv) > 1 else {}, indent=1))
