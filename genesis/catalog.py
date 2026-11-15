"""Catalog: packs (what Genesis tracks) and lock files (the newest resolved versions)."""

import glob
import os
import platform as _platform

from .resolvers import EXPANDERS, RESOLVERS, kiwix_id
from .util import ROOT, read_json, write_json

TIERS = ["seed", "ark", "eden"]
PLATFORMS = ["linux-x64", "linux-arm64", "windows-x64", "macos-arm64", "android"]
PACK_DIR = os.path.join(ROOT, "catalog", "packs")
LOCK_DIR = os.path.join(ROOT, "catalog", "lock")
ANY_LANG = (None, "", "mul", "zxx")


def host_platform():
    system, machine = _platform.system().lower(), _platform.machine().lower()
    arch = "arm64" if machine in ("aarch64", "arm64") else "x64"
    return {"linux": "linux", "darwin": "macos", "windows": "windows"}.get(system, "linux") + "-" + arch


def pack_names():
    return sorted(os.path.basename(p)[:-5] for p in glob.glob(os.path.join(PACK_DIR, "*.json")))


def load_pack(name):
    """Load one pack and normalize its items (defaults, shorthand, ids)."""
    pack = read_json(os.path.join(PACK_DIR, name + ".json"))
    if pack is None:
        raise SystemExit("no such pack: %s (have: %s)" % (name, ", ".join(pack_names())))
    items = []
    for raw in pack.get("items", []):
        item = dict(raw)
        if "zim" in item:  # shorthand for a single Kiwix book
            name_, flav = item.pop("zim"), item.pop("flavour", "")
            item["source"] = {"type": "kiwix", "name": name_, "flavour": flav}
            item.setdefault("id", kiwix_id(name_, flav))
        item.setdefault("tier", pack.get("tier", "ark"))
        item.setdefault("kind", pack.get("kind", "file"))
        item.setdefault("dest", pack.get("dest", name))
        items.append(item)
    pack["items"] = items
    pack["pack"] = name
    return pack


def lint():
    """Return a list of problems found in the pack files (empty means clean)."""
    problems, seen = [], {}
    for name in pack_names():
        try:
            pack = load_pack(name)
        except ValueError as e:
            problems.append("%s: not valid JSON: %s" % (name, e))
            continue
        for i, item in enumerate(pack["items"]):
            where = "%s[%d] %s" % (name, i, item.get("id", "?"))
            src = item.get("source") or {}
            if not item.get("id"):
                problems.append(where + ": missing id")
            elif item["id"] in seen:
                problems.append(where + ": id also used in " + seen[item["id"]])
            else:
                seen[item["id"]] = name
            if item.get("tier") not in TIERS:
                problems.append(where + ": tier must be one of " + ", ".join(TIERS))
            if src.get("type") not in RESOLVERS and src.get("type") not in EXPANDERS:
                problems.append(where + ": unknown source type %r" % src.get("type"))
            if src.get("type") not in EXPANDERS and src.get("type") != "kiwix" and not item.get("title"):
                problems.append(where + ": missing title")
            for plat in (src.get("assets") or src.get("files") or {}):
                if plat not in PLATFORMS + ["any", "source"]:
                    problems.append(where + ": unknown platform %r" % plat)
    return problems


def load_lock(name):
    data = read_json(os.path.join(LOCK_DIR, name + ".json"), {})
    return data.get("items", {})


def save_lock(name, items):
    write_json(os.path.join(LOCK_DIR, name + ".json"), {"pack": name, "items": items})


def all_locked(packs=None):
    """Yield (pack, id, entry) for every resolved item."""
    for name in pack_names():
        if packs and name not in packs:
            continue
        for iid, entry in load_lock(name).items():
            yield name, iid, entry


def tier_includes(selected, item_tier):
    return TIERS.index(item_tier) <= TIERS.index(selected)


def wanted_files(entry, platforms):
    """Files of an entry that apply to the chosen platforms."""
    return [f for f in entry.get("files", [])
            if f.get("platform") in (None, "any") or f.get("platform") in platforms]


def select(tier="seed", packs=None, ids=None, langs=None, platforms=None, tags=None):
    """Pick resolved items for a tier. Explicit ids bypass tier and language filters."""
    platforms = platforms or [host_platform(), "android"]
    langs = langs or ["eng"]
    out, index = [], {}
    for pack, iid, entry in all_locked(packs):
        if entry.get("status") == "error":
            continue
        if iid in index:  # the same item can sit in two packs; the lower tier wins
            if TIERS.index(entry.get("tier", "eden")) >= TIERS.index(out[index[iid]][2].get("tier", "eden")):
                continue
            out[index[iid]] = None
            del index[iid]
        if ids:
            if iid not in ids:
                continue
        else:
            if not tier_includes(tier, entry.get("tier", "eden")):
                continue
            if ("*" not in langs and not entry.get("any_lang") and entry.get("lang") not in ANY_LANG
                    and entry["lang"] not in langs):
                continue
            if tags and not (set(tags) & set(entry.get("tags") or [])):
                continue
        index[iid] = len(out)
        out.append((pack, iid, entry, wanted_files(entry, platforms)))
    return [o for o in out if o is not None]


def entry_size(entry, files=None):
    """Known download size in bytes (None when the source publishes no size)."""
    if entry.get("size"):
        return entry["size"]
    sizes = [f.get("size") for f in (entry.get("files", []) if files is None else files)]
    known = [s for s in sizes if s]
    return sum(known) if known else None
