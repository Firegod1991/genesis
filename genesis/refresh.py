"""Monthly refresh: resolve every catalog line to its newest version and rewrite the lock files."""

import datetime
import os
from concurrent.futures import ThreadPoolExecutor

from . import catalog
from .resolvers import ResolveError, resolve
from .util import ROOT, FetchError, Net, http_open, human, log, write_json

KEEP = ("title", "tier", "kind", "dest", "lang", "license", "tags", "post", "note", "rule", "home", "any_lang")
EMPTY = (None, "", [], {})


def _try(item, net):
    try:
        return True, resolve(item, net)
    except ResolveError as e:
        return False, str(e)
    except Exception as e:  # a resolver bug must not sink the whole refresh
        return False, "%s: %s" % (type(e).__name__, e)


def _entry(item, res):
    e = {k: item[k] for k in KEEP if item.get(k) not in EMPTY}
    e.update({k: v for k, v in res.items() if v not in EMPTY})
    return e


def _says_gone(error):
    return getattr(error.__cause__, "code", None) in (404, 410)


def _gone(url):
    """A HEAD came back 404. A few servers only answer GET, so ask for the first byte before believing it."""
    try:
        http_open(url, headers={"Range": "bytes=0-0"}, retries=1).close()
    except FetchError as e:
        return _says_gone(e)
    return False


def _fill_sizes(entry, net):
    """Ask the server for sizes the source did not publish, so `genesis plan` can add them up.

    Returns the links the server says it does not have. A checksum list can name a file its publisher hands
    out some other way, by torrent say, and that has to show in the report, not in somebody's failed download."""
    files, gone = entry.get("files") or [], []
    if entry.get("kind") == "bible" or len(files) > 8:
        return gone
    for f in files:
        if not f.get("size"):
            try:
                _, h = net.head(f["url"])
                if h.get("content-length", "").isdigit() and int(h["content-length"]) > 0:
                    f["size"] = int(h["content-length"])
            except Exception as e:
                if _says_gone(e) and _gone(f["url"]):
                    gone.append(f["url"])
    return gone


def _drop_gone(new, gone, failures, warnings):
    """Take out the files a server does not have. A line left with nothing to fetch is offered to nobody."""
    for iid, urls in gone.items():
        entry = new[iid]
        said = "the server does not have " + ", ".join(urls)
        left = [f for f in entry["files"] if f["url"] not in urls]
        if left:
            entry["files"] = left
            warnings.append((iid, said))
            continue
        failures.append((iid, said))
        new[iid] = {"title": entry.get("title", iid), "tier": entry["tier"], "kind": entry["kind"],
                    "status": "error", "error": said}


def _days(earlier, later):
    """Days from one ISO date to another, or 0 when either cannot be read."""
    try:
        return (datetime.date.fromisoformat(later) - datetime.date.fromisoformat(earlier)).days
    except (TypeError, ValueError):
        return 0


def merged(entries):
    """Volumes that a newer combined volume has replaced: {combined id: [the parts it took over]}.

    A publisher now and then folds several volumes into one and goes on listing the old ones. Project
    Gutenberg's shelves C, D, E and F became one volume, CDEF, in November 2026. A combined volume is
    told from an ordinary one (PA is a shelf of its own, not P plus A) by three things at once: its
    code spells out three or more volumes of the same rule that all exist, every one of them is a
    generation older, and it is about as big as all of them together.
    """
    def size(e):
        return catalog.entry_size(e) or 0
    out = {}
    for iid, e in entries.items():
        stem, dash, code = iid.rpartition("-")
        if not dash or not e.get("rule") or len(code) < 3 or not code.isalpha() or len(set(code)) != len(code):
            continue
        parts = [stem + "-" + letter for letter in code]
        if not all(p in entries and entries[p].get("rule") == e["rule"] for p in parts):
            continue
        if all(_days(entries[p].get("date"), e.get("date")) >= 45 for p in parts) and \
                size(e) >= 0.5 * sum(size(entries[p]) for p in parts):
            out[iid] = parts
    return out


def _fold(new, old):
    """Drop the parts a combined volume replaced, and have that volume say so in the lock.

    A drive that already holds the parts removes them once the combined volume is on it. The note
    stays for as long as the parts stay gone, so a drive that skips a few months still learns of it.
    """
    for iid, e in new.items():
        before = [p for p in (old.get(iid) or {}).get("replaces", []) if p not in new]
        if before:
            e["replaces"] = before
    for iid, parts in merged(new).items():
        new[iid]["replaces"] = sorted(set(new[iid].get("replaces", []) + parts))
        for p in parts:
            new.pop(p, None)


def refresh_pack(name, net, jobs=8):
    pack = catalog.load_pack(name)
    old = catalog.load_lock(name)
    new, failures, warnings = {}, [], []
    with ThreadPoolExecutor(max_workers=jobs) as pool:
        futures = [pool.submit(_try, item, net) for item in pack["items"]]
    for item, fut in zip(pack["items"], futures):
        ok, result = fut.result()
        if ok:
            for concrete, res in result:
                iid = concrete["id"]
                if iid in new:  # the first line that yields an id wins, so the lowest tier sticks
                    continue
                for w in res.pop("warnings", []):
                    warnings.append((iid, w))
                new[iid] = _entry(concrete, res)
            continue
        failures.append((item["id"], result))
        kept = False
        for iid, e in old.items():  # keep the last known good answer, flagged stale
            if (iid == item["id"] or e.get("rule") == item["id"]) and e.get("status") != "error":
                new.setdefault(iid, dict(e, stale=True))
                kept = True
        if not kept:
            new[item["id"]] = {"title": item.get("title", item["id"]), "tier": item["tier"],
                               "kind": item["kind"], "status": "error", "error": result}

    with ThreadPoolExecutor(max_workers=jobs) as pool:
        asked = list(pool.map(lambda e: _fill_sizes(e, net), list(new.values())))
    _drop_gone(new, {iid: urls for iid, urls in zip(list(new), asked) if urls}, failures, warnings)
    _fold(new, old)

    def ver(e):
        return (e.get("version"), e.get("status"))
    added = sorted(i for i in new if i not in old)
    removed = sorted(i for i in old if i not in new)
    updated = sorted(i for i in new if i in old and ver(new[i]) != ver(old[i]))
    catalog.save_lock(name, new)
    return {"pack": name, "title": pack.get("title", name), "items": new, "old": old, "added": added,
            "removed": removed, "updated": updated, "failures": failures, "warnings": warnings}


def tier_totals(packs=None):
    """Known bytes and item counts per cumulative tier (English, Linux x64 plus Android)."""
    out = {}
    for tier in catalog.TIERS:
        sel = catalog.select(tier=tier, packs=packs, langs=["eng"], platforms=["linux-x64", "android"])
        sizes = [catalog.entry_size(e, files) for _, _, e, files in sel]
        out[tier] = {"items": len(sel), "bytes": sum(s for s in sizes if s),
                     "unsized": sum(1 for s in sizes if not s)}
    return out


def _row(iid, e):
    size = human(catalog.entry_size(e)) if catalog.entry_size(e) else ""
    flag = " (stale)" if e.get("stale") else ""
    return "| %s | `%s` | %s | %s%s | %s | %s |" % (
        (e.get("title") or iid).replace("|", "/"), iid, e.get("tier", ""), e.get("version", ""), flag,
        size, (e.get("license") or "").replace("|", "/")[:60])


def render_catalog(results, today):
    lines = ["# Genesis catalog", "",
             "Generated by `genesis refresh` on %s. Do not edit by hand." % today, "",
             "Tiers are cumulative: **seed** is the essentials, **ark** adds the full working set, "
             "**eden** adds everything.", ""]
    totals = tier_totals()
    lines += ["| Tier | Items | Known size | Items without a published size |", "|---|---|---|---|"]
    for tier in catalog.TIERS:
        t = totals[tier]
        lines.append("| %s | %d | %s | %d |" % (tier, t["items"], human(t["bytes"]), t["unsized"]))
    lines += ["", "Sizes count English content for Linux x64 plus Android apps. "
              "Other languages and platforms add to it.", ""]
    for r in results:
        items = r["items"]
        lines += ["## %s" % r["title"], ""]
        singles = [(i, e) for i, e in items.items() if not e.get("rule") and e.get("status") != "error"]
        if singles:
            lines += ["| Item | Id | Tier | Version | Size | License |", "|---|---|---|---|---|---|"]
            lines += [_row(i, e) for i, e in singles]
            lines.append("")
        rules = {}
        for i, e in items.items():
            if e.get("rule"):
                rules.setdefault(e["rule"], []).append((i, e))
        for rule, group in rules.items():
            sizes = [catalog.entry_size(e) for _, e in group]
            known = sum(s for s in sizes if s)
            lines += ["<details><summary><b>%s</b>: %d items%s, tier %s</summary>" % (
                rule, len(group), (", " + human(known)) if known else "", group[0][1].get("tier", "")), ""]
            lines += ["* %s (`%s`, %s)" % ((e.get("title") or i), i, e.get("version", "")) for i, e in group]
            lines += ["", "</details>", ""]
    return "\n".join(lines) + "\n"


def render_report(results, today):
    lines = ["# Catalog refresh %s" % today, "",
             "| Pack | Items | New | Updated | Removed | Failed |", "|---|---|---|---|---|---|"]
    for r in results:
        lines.append("| %s | %d | %d | %d | %d | %d |" % (
            r["pack"], len(r["items"]), len(r["added"]), len(r["updated"]), len(r["removed"]),
            len(r["failures"])))
    lines.append("")
    for r in results:
        if r["updated"]:
            lines += ["## Updated in %s" % r["pack"], ""]
            for i in r["updated"][:150]:
                lines.append("* `%s`: %s to %s" % (i, r["old"][i].get("version", "?"),
                                                   r["items"][i].get("version", "?")))
            if len(r["updated"]) > 150:
                lines.append("* and %d more" % (len(r["updated"]) - 150))
            lines.append("")
        if r["added"] and r["old"]:
            lines += ["## New in %s" % r["pack"], ""]
            lines += ["* `%s`: %s" % (i, r["items"][i].get("title", "")) for i in r["added"][:150]]
            if len(r["added"]) > 150:
                lines.append("* and %d more" % (len(r["added"]) - 150))
            lines.append("")
    fails = [(r["pack"], i, msg) for r in results for i, msg in r["failures"]]
    if fails:
        lines += ["## Failed", "", "These lines kept their last known good version where one existed.", ""]
        lines += ["* **%s** `%s`: %s" % (p, i, msg[:600]) for p, i, msg in fails]
        lines.append("")
    warns = [(r["pack"], i, msg) for r in results for i, msg in r["warnings"]]
    if warns:
        lines += ["## Warnings", ""]
        lines += ["* **%s** `%s`: %s" % (p, i, msg[:600]) for p, i, msg in warns]
        lines.append("")
    return "\n".join(lines) + "\n"


def refresh(packs=None, jobs=8, write_docs=True):
    net = Net()
    today = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")
    results = []
    for name in packs or catalog.pack_names():
        log("refresh %s ..." % name)
        r = refresh_pack(name, net, jobs)
        log("  %d items, %d new, %d updated, %d failed" % (
            len(r["items"]), len(r["added"]), len(r["updated"]), len(r["failures"])))
        for i, msg in r["failures"]:
            log("  FAILED %s: %s" % (i, msg[:300]))
        results.append(r)
    if write_docs and not packs:
        write_json(os.path.join(catalog.LOCK_DIR, "_meta.json"), {
            "refreshed": today, "tiers": tier_totals(),
            "packs": {r["pack"]: len(r["items"]) for r in results}})
        with open(os.path.join(ROOT, "docs", "CATALOG.md"), "w", encoding="utf-8") as f:
            f.write(render_catalog(results, today))
        os.makedirs(os.path.join(ROOT, "reports"), exist_ok=True)
        with open(os.path.join(ROOT, "reports", "latest.md"), "w", encoding="utf-8") as f:
            f.write(render_report(results, today))
    return results
