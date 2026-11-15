"""Resolvers: turn a catalog source into the newest concrete download.

Every resolver takes (source dict, Net) and returns a dict:
    {"version": str, "date": str or None, "files": [ {url, filename, ...} ]}
File dicts may carry size, sha256, sha512, md5, sha256_url, md5_url, platform, role.

Rule resolvers (kiwix_rule, ebible) expand one catalog line into many items and
return a list of (item overrides, resolved) pairs instead.
"""

import base64
import csv
import email.utils
import io
import re
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from html import unescape

from .util import FetchError, _headers, fkey, http_open, parse_checksums, vkey


class ResolveError(Exception):
    pass


def _name(url):
    return urllib.parse.unquote(url.split("?")[0].rstrip("/").rsplit("/", 1)[1])


def _version_from(filename, src):
    rx = src.get("version_regex") or r"(\d{4}[.-]\d{2}[.-]\d{2}|\d+(?:\.\d+)+(?:-\d+)?)"
    m = re.search(rx, filename)
    return m.group(1) if m else filename


# ---------------------------------------------------------------- Kiwix

KIWIX_CATALOG = "https://library.kiwix.org/catalog/v2/entries?count=-1"
_ATOM = {"a": "http://www.w3.org/2005/Atom"}


def kiwix_books(net):
    """Whole Kiwix library keyed by (name, flavour). One download per run."""
    cached = getattr(net, "_kiwix", None)
    if cached is not None:
        return cached
    root = ET.fromstring(net.get(KIWIX_CATALOG))
    books = {}
    for e in root.findall("a:entry", _ATOM):
        def g(tag, e=e):
            return (e.findtext("a:" + tag, default="", namespaces=_ATOM) or "").strip()
        url, size = "", None
        for link in e.findall("a:link", _ATOM):
            if link.get("type") == "application/x-zim":
                url = link.get("href", "")
                size = int(link.get("length") or 0) or None
        if not url:
            continue
        url = re.sub(r"\.meta4$", "", url).replace("//lb.download.kiwix.org/", "//download.kiwix.org/")
        b = {"name": g("name"), "flavour": g("flavour"), "title": g("title"),
             "summary": g("summary"), "lang": g("language"), "category": g("category"),
             "updated": g("updated")[:10], "url": url, "size": size}
        key = (b["name"], b["flavour"])
        if key not in books or books[key]["updated"] < b["updated"]:
            books[key] = b
    if not books:
        raise ResolveError("Kiwix catalog came back empty")
    net._kiwix = books
    return books


def _kiwix_resolved(b):
    m = re.search(r"_(\d{4}-\d{2})\.zim$", b["url"])
    return {"version": m.group(1) if m else b["updated"], "date": b["updated"],
            "files": [{"url": b["url"], "filename": _name(b["url"]), "size": b["size"],
                       "sha256_url": b["url"] + ".sha256"}]}


def kiwix_id(name, flavour):
    return name + ("." + flavour if flavour else "")


def r_kiwix(src, net):
    books = kiwix_books(net)
    b = books.get((src["name"], src.get("flavour", "")))
    if not b:
        have = sorted(f for (n, f) in books if n == src["name"])
        raise ResolveError("no Kiwix book %r flavour %r (flavours available: %s)"
                           % (src["name"], src.get("flavour", ""), have or "none"))
    codes = b["lang"].split(",")
    return dict(_kiwix_resolved(b), hint={"title": b["title"],
                                          "lang": codes[0] if len(codes) == 1 else "mul"})


def x_kiwix_rule(src, net):
    rx = re.compile(src["regex"]) if src.get("regex") else None
    ex = re.compile(src["exclude"]) if src.get("exclude") else None
    langs = set(src.get("langs") or [])
    pref = src.get("flavours") or ["maxi", "", "nopic", "mini", "full"]
    cap = src.get("max_gb")
    best = {}
    for (name, flav), b in kiwix_books(net).items():
        if "category" in src and b["category"] != src["category"]:
            continue
        if src.get("prefix") and not name.startswith(src["prefix"]):
            continue
        if (rx and not rx.search(name)) or (ex and ex.search(name)) or flav not in pref:
            continue
        if langs and not (set(b["lang"].split(",")) & langs):
            continue
        if cap and (b["size"] or 0) > cap * 1e9:
            continue
        cur = best.get(name)
        if cur is None or pref.index(flav) < pref.index(cur["flavour"]):
            best[name] = b
    out = []
    for name in sorted(best):
        b = best[name]
        codes = b["lang"].split(",")
        over = {"id": kiwix_id(name, b["flavour"]), "title": b["title"],
                "lang": codes[0] if len(codes) == 1 else "mul"}
        out.append((over, _kiwix_resolved(b)))
    if not out:
        raise ResolveError("Kiwix rule matched nothing: %r" % src)
    return out


# ---------------------------------------------------------------- eBible

EBIBLE_CSV = "https://ebible.org/Scriptures/translations.csv"


def ebible_rows(net):
    text = net.get(EBIBLE_CSV).decode("utf-8-sig", "replace")
    return list(csv.DictReader(io.StringIO(text)))


def x_ebible(src, net):
    """select: ids (curated list), public_domain, or redistributable (everything eBible may share)."""
    select = src.get("select", "ids")
    ids = src.get("ids") or []
    out, seen = [], set()
    for r in ebible_rows(net):
        tid = r.get("translationId", "")
        if r.get("Redistributable") != "True" or r.get("downloadable") != "True" or not tid:
            continue
        public = "public domain" in (r.get("Copyright") or "").lower()
        if select == "ids" and tid not in ids:
            continue
        if select == "public_domain" and not public:
            continue
        seen.add(tid)
        base = "https://ebible.org/Scriptures/" + urllib.parse.quote(tid)
        short = r.get("shortTitle") or r.get("title") or tid
        over = {"id": "bible_" + tid, "lang": r.get("languageCode") or "mul",
                "title": "%s: %s" % (r.get("languageNameInEnglish") or "?", short),
                "license": "Public domain" if public else (r.get("Copyright") or "See eBible.org"),
                "order": ids.index(tid) if tid in ids else None,
                # Source language texts belong in every vault whatever languages the owner reads.
                "any_lang": True if tid in (src.get("always") or []) else None}

        def num(k, r=r):
            return int(r.get(k) or 0)
        res = {"version": r.get("UpdateDate") or "", "date": r.get("UpdateDate") or None,
               "files": [{"url": base + "_html.zip", "filename": tid + "_html.zip", "role": "html"},
                         {"url": base + "_usfm.zip", "filename": tid + "_usfm.zip", "role": "usfm"}],
               "meta": {"translation": tid, "language": r.get("languageNameInEnglish"),
                        "native": r.get("languageName"), "name": r.get("title"),
                        "script": r.get("script"), "direction": r.get("textDirection") or "ltr",
                        "ot": num("OTbooks"), "nt": num("NTbooks"), "dc": num("DCbooks"),
                        "public_domain": public}}
        out.append((over, res))
    missing = [i for i in ids if i not in seen] if select == "ids" else []
    if missing:
        raise ResolveError("eBible ids not found or not redistributable: %s" % ", ".join(missing))
    if not out:
        raise ResolveError("eBible rule matched nothing")
    if select == "ids":
        out.sort(key=lambda p: p[0]["order"])
    return out


# ---------------------------------------------------------------- checksum lists

def _latest_dir(net, index):
    html = net.text(index["url"])
    dirs = re.findall(index["pattern"], html)
    if not dirs:
        raise ResolveError("no directories matched %r at %s" % (index["pattern"], index["url"]))
    return max(set(dirs), key=fkey)


def _pick_checksums(net, url, base, patterns, algo, src):
    entries = parse_checksums(net.text(url))
    files, missing = [], []
    for platform, pattern in patterns.items():
        rx = re.compile(pattern)
        cands = [(h, f) for h, f in entries if rx.search(f)]
        if not cands:
            missing.append("%s (%s)" % (platform, pattern))
            continue
        h, f = max(cands, key=lambda t: fkey(t[1]))
        entry = {"url": urllib.parse.urljoin(base, f.lstrip("./")), "filename": f.rsplit("/", 1)[-1], algo: h}
        if platform != "any":
            entry["platform"] = platform
        files.append(entry)
    if not files:
        raise ResolveError("nothing in %s matched %s; first names: %s"
                           % (url, missing, [f for _, f in entries[:8]]))
    res = {"version": _version_from(files[0]["filename"], src), "date": None, "files": files}
    if missing:
        res["warnings"] = ["no file for " + ", ".join(missing)]
    return res


def r_checksum_file(src, net):
    """A published checksum list (SHA256SUMS and friends). {dir} is the newest directory of `index`."""
    url = src["url"]
    if "index" in src:
        url = url.replace("{dir}", _latest_dir(net, src["index"]))
    base = src.get("base", url.rsplit("/", 1)[0] + "/")
    patterns = src.get("files") or {"any": src["pattern"]}
    return _pick_checksums(net, url, base, patterns, src.get("algo", "sha256"), src)


def r_ubuntu(src, net):
    """Newest supported Ubuntu LTS. host: releases (amd64) or cdimage (arm64, Raspberry Pi)."""
    blocks = net.text("https://changelogs.ubuntu.com/meta-release-lts").strip().split("\n\n")
    series = None
    for b in blocks:
        f = dict(line.split(": ", 1) for line in b.splitlines() if ": " in line)
        if f.get("Supported") == "1" and f.get("Version"):
            series = re.match(r"(\d+\.\d+)", f["Version"]).group(1)
    if not series:
        raise ResolveError("no supported Ubuntu LTS found")
    if src.get("host") == "cdimage":
        url = "https://cdimage.ubuntu.com/releases/%s/release/SHA256SUMS" % series
    else:
        url = "https://releases.ubuntu.com/%s/SHA256SUMS" % series
    return _pick_checksums(net, url, url.rsplit("/", 1)[0] + "/", {"any": src["pattern"]}, "sha256", src)


def r_openwrt(src, net):
    ver = net.json("https://downloads.openwrt.org/.versions.json")["stable_version"]
    base = "https://downloads.openwrt.org/releases/%s/targets/%s/" % (ver, src["target"])
    return _pick_checksums(net, base + "sha256sums", base, {"any": src["pattern"]}, "sha256",
                           dict(src, version_regex=r"(\d+\.\d+\.\d+)"))


# ---------------------------------------------------------------- distro specific

def r_tails(src, net):
    d = net.json("https://tails.net/install/v2/Tails/amd64/stable/latest.json")
    inst = d["installations"][0]
    for path in inst["installation-paths"]:
        if path["type"] == src.get("image", "img"):
            t = path["target-files"][0]
            return {"version": str(inst.get("version") or _version_from(t["url"], src)), "date": None,
                    "files": [{"url": t["url"], "filename": _name(t["url"]), "size": t.get("size"),
                               "sha256": t.get("sha256")}]}
    raise ResolveError("Tails image type %r not offered" % src.get("image", "img"))


def r_rpi_imager(src, net):
    d = net.json("https://downloads.raspberrypi.com/os_list_imagingutility_v4.json")
    rx = re.compile(src["name"])

    def walk(items):
        for i in items:
            if "subitems" in i:
                yield from walk(i["subitems"])
            elif "url" in i:
                yield i
    for i in walk(d["os_list"]):
        if rx.search(i.get("name", "")):
            f = {"url": i["url"], "filename": _name(i["url"]), "sha256_url": i["url"] + ".sha256"}
            if str(i.get("image_download_size", "")).isdigit():
                f["size"] = int(i["image_download_size"])
            if i.get("image_download_sha256"):
                f["sha256"] = i["image_download_sha256"]
                f.pop("sha256_url")
            return {"version": i.get("release_date") or _version_from(f["filename"], src),
                    "date": i.get("release_date"), "files": [f]}
    raise ResolveError("no Raspberry Pi image named like %r" % src["name"])


def r_alpine(src, net):
    arch = src.get("arch", "x86_64")
    base = "https://dl-cdn.alpinelinux.org/alpine/latest-stable/releases/%s/" % arch
    text = net.text(base + "latest-releases.yaml")
    for block in re.split(r"(?m)^-\s*$", text):
        f = dict(re.findall(r"(?m)^\s+(\w+):\s*\"?([^\"\n|]+?)\"?\s*$", block))
        if f.get("flavor") == src.get("flavor", "alpine-standard") and f.get("file"):
            return {"version": f.get("version", ""), "date": f.get("date"),
                    "files": [{"url": base + f["file"], "filename": f["file"],
                               "size": int(f["size"]) if f.get("size", "").isdigit() else None,
                               "sha256": f.get("sha256")}]}
    raise ResolveError("Alpine flavor %r not found" % src.get("flavor"))


def r_fedora(src, net):
    rel = [r for r in net.json("https://fedoraproject.org/releases.json")
           if r.get("version", "").isdigit() and r.get("arch") == src.get("arch", "x86_64")]
    rx = re.compile(src["link"])
    rel = [r for r in rel if rx.search(r.get("link", ""))]
    if not rel:
        raise ResolveError("no Fedora image matched %r" % src["link"])
    r = max(rel, key=lambda r: int(r["version"]))
    return {"version": r["version"], "date": None,
            "files": [{"url": r["link"], "filename": _name(r["link"]), "sha256": r.get("sha256"),
                       "size": int(r["size"]) if str(r.get("size", "")).isdigit() else None}]}


def _sf_file(project, path, platform, size, md5):
    f = {"url": "https://downloads.sourceforge.net/project/%s%s" % (project, urllib.parse.quote(path)),
         "filename": path.rsplit("/", 1)[-1]}
    if size:
        f["size"] = int(size)
    if md5:
        f["md5"] = md5
    if platform != "any":
        f["platform"] = platform
    return f


def _sf_feed(net, project, path):
    """Files under a SourceForge folder as its file feed lists them: path, date, size, md5."""
    url = "https://sourceforge.net/projects/%s/rss?path=%s&limit=100" % (project, urllib.parse.quote(path))
    out = []
    for item in re.findall(r"(?s)<item>(.*?)</item>", net.text(url)):
        title = re.search(r"(?s)<title>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</title>", item)
        if not title:
            continue
        when = re.search(r"<pubDate>(.*?)</pubDate>", item)
        md5 = re.search(r'<media:hash algo="md5">([0-9a-f]{32})</media:hash>', item)
        size = re.search(r'filesize="(\d+)"', item)
        try:
            date = email.utils.parsedate_to_datetime(when.group(1)).strftime("%Y-%m-%d")
        except (AttributeError, TypeError, ValueError):
            date = ""
        out.append({"path": unescape(title.group(1).strip()), "date": date,
                    "size": size.group(1) if size else None, "md5": md5.group(1) if md5 else None})
    return out


def r_sourceforge(src, net):
    """SourceForge. With path: the newest upload in that folder, per platform when files is given.
    Without: the download the project marks as its default, or with platforms its default for each
    operating system. A project's default can lag years behind its newest upload, so prefer path."""
    project = src["project"]
    files, missing, dates = [], [], []
    if src.get("path"):
        feed = _sf_feed(net, project, src["path"])
        for platform, pattern in (src.get("files") or {"any": src["pattern"]}).items():
            cands = [f for f in feed if re.search(pattern, f["path"])]
            if not cands:
                missing.append(platform)
                continue
            best = max(cands, key=lambda f: (f["date"], fkey(f["path"])))
            files.append(_sf_file(project, best["path"], platform, best["size"], best["md5"]))
            dates.append(best["date"])
    else:
        d = net.json("https://sourceforge.net/projects/%s/best_release.json" % project)
        per = d.get("platform_releases") or {}
        for platform, theirs in (src.get("platforms") or {"any": src.get("platform")}).items():
            rel = (per.get(theirs) if theirs else d.get("release")) or {}
            fn = rel.get("filename")
            if not fn or (src.get("pattern") and not re.search(src["pattern"], fn)):
                missing.append(platform)
                continue
            files.append(_sf_file(project, fn, platform, rel.get("bytes"), rel.get("md5sum")))
            dates.append((rel.get("date") or "")[:10])
    if not files:
        raise ResolveError("SourceForge project %r has no download matching %r"
                           % (project, src.get("files") or src.get("pattern")))
    res = {"version": _version_from(files[0]["filename"], src), "date": max(dates) or None, "files": files}
    if missing:
        res["warnings"] = ["no file for " + ", ".join(missing)]
    return res


def r_fdroid(src, net):
    pkg = src["package"]
    d = net.json("https://f-droid.org/api/v1/packages/" + pkg)
    code = d.get("suggestedVersionCode")
    if not code:
        raise ResolveError("F-Droid does not list %s" % pkg)
    name = next((p.get("versionName") for p in d.get("packages", []) if p.get("versionCode") == code), str(code))
    fn = "%s_%s.apk" % (pkg, code)
    return {"version": name, "date": None,
            "files": [{"url": "https://f-droid.org/repo/" + fn, "filename": fn, "platform": "android"}]}


# ---------------------------------------------------------------- GitHub

def _gh_release(net, repo, tag, prerelease):
    api = "https://api.github.com/repos/%s/releases" % repo
    try:
        if tag:
            return net.json(api + "/tags/" + tag)
        if prerelease:
            rels = net.json(api + "?per_page=10")
            if rels:
                return rels[0]
            raise ResolveError("%s has no releases" % repo)
        return net.json(api + "/latest")
    except FetchError as api_error:
        # No token or rate limited: read the public release page instead.
        try:
            if not tag:
                final, _ = net.head("https://github.com/%s/releases/latest" % repo)
                tag = final.rsplit("/tag/", 1)[1]
            html = net.text("https://github.com/%s/releases/expanded_assets/%s" % (repo, tag))
        except (FetchError, IndexError):
            raise ResolveError("GitHub release lookup failed for %s: %s" % (repo, api_error))
        names = re.findall(r'href="/%s/releases/download/[^"]+/([^"/]+)"' % re.escape(repo), html)
        return {"tag_name": urllib.parse.unquote(tag), "assets": [
            {"name": urllib.parse.unquote(n),
             "browser_download_url": "https://github.com/%s/releases/download/%s/%s" % (repo, tag, n)}
            for n in dict.fromkeys(names)]}


def _gh_recent(net, repo):
    """Recent releases, newest first, drafts left out. Empty when the list cannot be read."""
    try:
        rels = net.json("https://api.github.com/repos/%s/releases?per_page=30" % repo)
    except FetchError:
        return []
    return [r for r in rels if isinstance(r, dict) and r.get("tag_name") and not r.get("draft")]


def _gh_earlier(older, tag, anywhere):
    """The releases a file missing from release `tag` may be taken from, best first."""
    if anywhere:  # scan: the newest listed release that has the file, whatever is marked latest
        return [r for r in older if r["tag_name"] != tag]
    tags = [r["tag_name"] for r in older]
    earlier = older[tags.index(tag) + 1:] if tag in tags else older
    return [r for r in earlier if not r.get("prerelease")]


def _gh_match(rel, pattern):
    cands = [a for a in rel.get("assets") or [] if re.search(pattern, a["name"])]
    return max(cands, key=lambda a: fkey(a["name"])) if cands else None


_NOISE = re.compile(r"(?i)\.(sig|asc|sha\d+(sum)?|md5|digest|zsync|jsonl|pem|sbom|intoto|blockmap)(\.|$)")
_HINTS = {"windows-x64": r"(?i)win|\.exe$|\.msi$", "macos-arm64": r"(?i)mac|darwin|osx|apple|\.dmg$",
          "linux-x64": r"(?i)linux|\.deb$|\.rpm$|appimage", "linux-arm64": r"(?i)aarch64|arm64",
          "android": r"(?i)android|\.apk$"}


def _gh_names(rel, platforms=(), limit=12):
    """A short list of asset names for the report: no signatures, likely names for the platforms first."""
    names = [a["name"] for a in rel.get("assets") or [] if not _NOISE.search(a["name"])]
    hints = [re.compile(_HINTS[p]) for p in platforms if p in _HINTS]
    names.sort(key=lambda n: not any(h.search(n) for h in hints))
    return names[:limit] + (["and %d more" % (len(names) - limit)] if len(names) > limit else [])


def _gh_version(tag):
    """v1.2.3, jq-1.8.2 and llvmorg-23.1.5 are versions with a label in front: drop the label."""
    m = re.match(r"(?:[A-Za-z][\w.-]*?[-_/])?[vV]?(\d.*)$", tag)
    return m.group(1) if m else tag


def r_github(src, net):
    """Newest GitHub release. assets maps platform to a regex; asset is a single regex.

    A file the newest release lacks (a point release for some boards only) comes from the newest
    earlier stable release that has it, and the report says so: the line keeps working while
    someone looks at the pattern. With scan, the release marked latest is only a hint and the
    newest listed release that has the files wins, for projects that publish builds apart from it.
    """
    repo, pinned = src["repo"], src.get("tag")
    wanted = src.get("assets") or ({"any": src["asset"]} if src.get("asset") else {})
    older = None
    try:
        rel = _gh_release(net, repo, pinned, src.get("prerelease"))
    except ResolveError:
        older = [] if pinned else _gh_recent(net, repo)  # nothing marked latest: take the newest listed
        if not older:
            raise
        rel = older[0]
    tag = rel["tag_name"]
    files, used, borrowed, missing = [], [], [], []
    for platform, pattern in wanted.items():
        asset, origin = _gh_match(rel, pattern), rel
        if asset is None and not pinned:
            if older is None:
                older = _gh_recent(net, repo)
            for old in _gh_earlier(older, tag, src.get("scan") or src.get("prerelease")):
                found = _gh_match(old, pattern)
                if found is not None:
                    asset, origin = found, old
                    break
        if asset is None:
            missing.append(platform)
            continue
        f = {"url": asset["browser_download_url"], "filename": asset["name"]}
        if asset.get("size"):
            f["size"] = asset["size"]
        if str(asset.get("digest") or "").startswith("sha256:"):
            f["sha256"] = asset["digest"][7:]
        if platform != "any":
            f["platform"] = platform
        files.append(f)
        used.append(asset)
        if origin is not rel:
            borrowed.append((platform, origin))
    # The release that names the version: the newest one, unless every file came from an earlier one.
    main = rel
    if borrowed and len(borrowed) == len(files):
        main = min((o for _, o in borrowed), key=lambda o: [r["tag_name"] for r in older].index(o["tag_name"]))
    if src.get("source"):
        files.append({"url": "https://github.com/%s/archive/refs/tags/%s.tar.gz" % (repo, tag),
                      "filename": "%s-%s.tar.gz" % (repo.split("/")[1], tag.replace("/", "_")),
                      "platform": "source"})
    if not files:
        raise ResolveError("no asset in %s %s matched %s; assets: %s"
                           % (repo, tag, list(wanted.values()), _gh_names(rel, wanted)))
    date = (main.get("published_at") or "")[:10] or None
    if pinned:  # a rolling tag (nightly, latest) swaps files under one name: date it by the newest upload
        date = max((a.get("updated_at") or "" for a in used), default="")[:10] or date
    res = {"version": (date or tag) if pinned else _gh_version(main["tag_name"]), "date": date, "files": files}
    warnings = []
    if main is not rel and not src.get("scan"):
        warnings.append("newest release %s has none of the files, using %s; assets there: %s"
                        % (tag, main["tag_name"], _gh_names(rel, wanted)))
    late = ["%s from %s" % (p, o["tag_name"]) for p, o in borrowed if o is not main]
    if late:
        warnings.append("not in %s, taken from an earlier release: %s; assets there: %s"
                        % (main["tag_name"], ", ".join(late), _gh_names(main, [p for p, _ in borrowed])))
    if missing:
        warnings.append("no asset for %s; assets: %s" % (", ".join(missing), _gh_names(rel, missing)))
    if warnings:
        res["warnings"] = warnings
    return res


# ---------------------------------------------------------------- language toolchains

_GO = {"linux-x64": ("linux", "amd64"), "linux-arm64": ("linux", "arm64"),
       "windows-x64": ("windows", "amd64"), "macos-arm64": ("darwin", "arm64")}
_NODE = {"linux-x64": "linux-x64.tar.xz", "linux-arm64": "linux-arm64.tar.xz",
         "windows-x64": "win-x64.zip", "macos-arm64": "darwin-arm64.tar.xz"}
_RUST = {"linux-x64": "x86_64-unknown-linux-gnu", "linux-arm64": "aarch64-unknown-linux-gnu",
         "windows-x64": "x86_64-pc-windows-msvc", "macos-arm64": "aarch64-apple-darwin"}
_ZIG = {"linux-x64": "x86_64-linux", "linux-arm64": "aarch64-linux",
        "windows-x64": "x86_64-windows", "macos-arm64": "aarch64-macos"}
_JAVA = {"linux-x64": ("linux", "x64"), "linux-arm64": ("linux", "aarch64"),
         "windows-x64": ("windows", "x64"), "macos-arm64": ("mac", "aarch64")}
_JULIA = {"linux-x64": ("linux", "x86_64"), "linux-arm64": ("linux", "aarch64"),
          "windows-x64": ("winnt", "x86_64"), "macos-arm64": ("mac", "aarch64")}
_DOTNET = {"linux-x64": "linux-x64", "linux-arm64": "linux-arm64",
           "windows-x64": "win-x64", "macos-arm64": "osx-arm64"}
_DART = {"linux-x64": "linux-x64", "linux-arm64": "linux-arm64",
         "windows-x64": "windows-x64", "macos-arm64": "macos-arm64"}


def _tc_go(net):
    rel = next(r for r in net.json("https://go.dev/dl/?mode=json") if r.get("stable"))
    files = []
    for plat, (osn, arch) in _GO.items():
        for f in rel["files"]:
            if f["kind"] == "archive" and f["os"] == osn and f["arch"] == arch:
                files.append({"url": "https://go.dev/dl/" + f["filename"], "filename": f["filename"],
                              "size": f["size"], "sha256": f["sha256"], "platform": plat})
    return rel["version"].replace("go", ""), None, files


def _tc_node(net):
    lts = next(r for r in net.json("https://nodejs.org/dist/index.json") if r.get("lts"))
    v = lts["version"]
    base = "https://nodejs.org/dist/%s/" % v
    sums = {f: h for h, f in parse_checksums(net.text(base + "SHASUMS256.txt"))}
    files = []
    for plat, suffix in _NODE.items():
        fn = "node-%s-%s" % (v, suffix)
        if fn in sums:
            files.append({"url": base + fn, "filename": fn, "sha256": sums[fn], "platform": plat})
    return v.lstrip("v"), lts.get("date"), files


def _tc_rust(net):
    text = net.text("https://static.rust-lang.org/dist/channel-rust-stable.toml")
    m = re.search(r'(?m)^\[pkg\.rust\]\s*\nversion = "(\S+) \(\w+ (\d{4}-\d\d-\d\d)\)"', text)
    files = []
    for plat, triple in _RUST.items():
        sec = re.search(r'(?ms)^\[pkg\.rust\.target\.%s\]\s*\n(.*?)(?=^\[)' % re.escape(triple), text)
        if not sec:
            continue
        url = re.search(r'(?m)^xz_url = "([^"]+)"', sec.group(1))
        sha = re.search(r'(?m)^xz_hash = "([0-9a-f]{64})"', sec.group(1))
        if url and sha:
            files.append({"url": url.group(1), "filename": _name(url.group(1)),
                          "sha256": sha.group(1), "platform": plat})
    return m.group(1), m.group(2), files


def _tc_zig(net):
    d = net.json("https://ziglang.org/download/index.json")
    v = max((k for k in d if re.fullmatch(r"\d+\.\d+\.\d+", k)), key=vkey)
    files = []
    for plat, key in _ZIG.items():
        if key in d[v]:
            e = d[v][key]
            files.append({"url": e["tarball"], "filename": _name(e["tarball"]), "sha256": e["shasum"],
                          "size": int(e["size"]), "platform": plat})
    return v, d[v].get("date"), files


def _tc_java(net):
    lts = net.json("https://api.adoptium.net/v3/info/available_releases")["most_recent_lts"]
    rel = net.json("https://api.adoptium.net/v3/assets/latest/%s/hotspot?image_type=jdk&vendor=eclipse" % lts)
    files, version = [], str(lts)
    for plat, (osn, arch) in _JAVA.items():
        for a in rel:
            b = a["binary"]
            if b["os"] == osn and b["architecture"] == arch and b["image_type"] == "jdk":
                p = b["package"]
                version = a["version"].get("semver", version)
                files.append({"url": p["link"], "filename": p["name"], "size": p.get("size"),
                              "sha256": p.get("checksum"), "platform": plat})
                break
    return version, None, files


def _tc_julia(net):
    d = net.json("https://julialang-s3.julialang.org/bin/versions.json")
    v = max((k for k in d if d[k].get("stable")), key=vkey)
    files = []
    for plat, (osn, arch) in _JULIA.items():
        for f in d[v]["files"]:
            if f["os"] == osn and f["arch"] == arch and f["kind"] == "archive":
                files.append({"url": f["url"], "filename": _name(f["url"]), "size": f.get("size"),
                              "sha256": f.get("sha256"), "platform": plat})
                break
    return v, None, files


def _tc_dotnet(net):
    idx = net.json("https://builds.dotnet.microsoft.com/dotnet/release-metadata/releases-index.json")
    ch = next(c for c in idx["releases-index"]
              if c.get("release-type") == "lts" and c.get("support-phase") == "active")
    rel = net.json(ch["releases.json"])["releases"][0]
    files = []
    for plat, rid in _DOTNET.items():
        for f in rel["sdk"]["files"]:
            if f.get("rid") == rid and f["name"].endswith((".tar.gz", ".zip")):
                files.append({"url": f["url"], "filename": _name(f["url"]), "sha512": f.get("hash"),
                              "platform": plat})
                break
    return rel["sdk"]["version"], rel.get("release-date"), files


def _tc_dart(net):
    d = net.json("https://storage.googleapis.com/dart-archive/channels/stable/release/latest/VERSION")
    v = d["version"]
    base = "https://storage.googleapis.com/dart-archive/channels/stable/release/%s/sdk/" % v
    files = []
    for plat, key in _DART.items():
        fn = "dartsdk-%s-release.zip" % key
        files.append({"url": base + fn, "filename": "dartsdk-%s-%s.zip" % (v, key),
                      "sha256_url": base + fn + ".sha256sum", "platform": plat})
    return v, d.get("date"), files


_TOOLCHAINS = {"go": _tc_go, "node": _tc_node, "rust": _tc_rust, "zig": _tc_zig,
               "java": _tc_java, "julia": _tc_julia, "dotnet": _tc_dotnet, "dart": _tc_dart}


def r_toolchain(src, net):
    version, date, files = _TOOLCHAINS[src["lang"]](net)
    if not files:
        raise ResolveError("no downloads found for toolchain %s %s" % (src["lang"], version))
    return {"version": version, "date": date, "files": files}


# ---------------------------------------------------------------- maps, models, images, plain files

def r_protomaps(src, net):
    builds = net.json("https://build-metadata.protomaps.dev/builds.json")
    b = max(builds, key=lambda b: b["key"])
    f = {"url": "https://build.protomaps.com/" + b["key"], "filename": "planet.pmtiles", "size": b.get("size")}
    if b.get("md5sum"):
        f["md5"] = base64.b64decode(b["md5sum"]).hex()
    day = b["key"][:8]
    return {"version": day, "date": "%s-%s-%s" % (day[:4], day[4:6], day[6:8]), "files": [f],
            "meta": {"basemap_version": b.get("version")}}


def r_ollama(src, net):
    name, _, tag = src["model"].partition(":")
    lib = name if "/" in name else "library/" + name
    try:
        d = net.json("https://registry.ollama.ai/v2/%s/manifests/%s" % (lib, tag or "latest"),
                     {"Accept": "application/vnd.docker.distribution.manifest.v2+json"})
    except FetchError as e:
        raise ResolveError("Ollama has no model %s (%s)" % (src["model"], e))
    if "layers" not in d:
        raise ResolveError("Ollama has no model %s" % src["model"])
    digest = d.get("config", {}).get("digest", "")
    return {"version": digest.replace("sha256:", "")[:12], "date": None, "files": [],
            "model": src["model"], "size": sum(l.get("size", 0) for l in d["layers"])}


_MANIFESTS = ", ".join(["application/vnd.oci.image.index.v1+json",
                        "application/vnd.docker.distribution.manifest.list.v2+json",
                        "application/vnd.oci.image.manifest.v1+json",
                        "application/vnd.docker.distribution.manifest.v2+json"])


def parse_image(image):
    """Split an image reference into (registry, repository, tag)."""
    ref, _, tag = image.rpartition(":") if ":" in image.rsplit("/", 1)[-1] else (image, "", "latest")
    first = ref.split("/", 1)[0]
    if "/" in ref and ("." in first or ":" in first or first == "localhost"):
        registry, repo = ref.split("/", 1)
    else:
        registry, repo = "docker.io", ref if "/" in ref else "library/" + ref
    return registry, repo, tag


def r_docker(src, net):
    """Resolve a container image tag to its current digest, which proves the image exists."""
    registry, repo, tag = parse_image(src["image"])
    host = "registry-1.docker.io" if registry == "docker.io" else registry
    url = "https://%s/v2/%s/manifests/%s" % (host, repo, tag)
    hdrs = {"Accept": _MANIFESTS}
    try:
        try:
            resp = urllib.request.urlopen(
                urllib.request.Request(url, method="HEAD", headers=_headers(url, hdrs)), timeout=45)
        except urllib.error.HTTPError as e:
            if e.code != 401:
                raise
            ch = e.headers.get("WWW-Authenticate", "")
            realm = re.search(r'realm="([^"]+)"', ch)
            service = re.search(r'service="([^"]*)"', ch)
            if not realm:
                raise
            q = {"scope": "repository:%s:pull" % repo}
            if service:
                q["service"] = service.group(1)
            tok = net.json(realm.group(1) + "?" + urllib.parse.urlencode(q))
            hdrs["Authorization"] = "Bearer " + (tok.get("token") or tok.get("access_token") or "")
            resp = http_open(url, "HEAD", hdrs)
        with resp:
            digest = resp.headers.get("Docker-Content-Digest") or ""
    except (urllib.error.URLError, FetchError, OSError) as e:
        raise ResolveError("image %s not resolvable: %s" % (src["image"], e))
    if not digest:
        raise ResolveError("registry gave no digest for %s" % src["image"])
    return {"version": digest.replace("sha256:", "")[:12], "date": None, "files": [],
            "image": src["image"], "digest": digest}


def r_static(src, net):
    """A fixed URL. With probe (default) a HEAD request records size and modification date."""
    f = {"url": src["url"], "filename": src.get("filename") or _name(src["url"])}
    for k in ("sha256", "md5", "sha256_url", "md5_url", "size", "platform"):
        if src.get(k):
            f[k] = src[k]
    version, date = src.get("version"), None
    if src.get("probe", True):
        _, h = net.head(src["url"])
        if h.get("content-length", "").isdigit() and int(h["content-length"]) > 0:
            f["size"] = int(h["content-length"])
        if h.get("last-modified"):
            from email.utils import parsedate_to_datetime
            try:
                date = parsedate_to_datetime(h["last-modified"]).strftime("%Y-%m-%d")
            except (TypeError, ValueError):
                date = None
    return {"version": version or date or "current", "date": date, "files": [f]}


def r_http_index(src, net):
    """A plain directory listing. dir_pattern (optional) picks the newest subdirectory first."""
    url = src["url"]
    if src.get("dir_pattern"):
        url = urllib.parse.urljoin(url, _latest_dir(net, {"url": url, "pattern": src["dir_pattern"]}) + "/")
    html = net.text(url)
    patterns = src.get("files") or {"any": src["pattern"]}
    files, missing = [], []
    for platform, pattern in patterns.items():
        names = [m if isinstance(m, str) else m[0] for m in re.findall(pattern, html)]
        if not names:
            missing.append(platform)
            continue
        fn = max(set(names), key=fkey)
        f = {"url": urllib.parse.urljoin(url, fn), "filename": fn.rsplit("/", 1)[-1]}
        if src.get("sha256_suffix"):
            f["sha256_url"] = f["url"] + src["sha256_suffix"]
        if platform != "any":
            f["platform"] = platform
        files.append(f)
    if not files:
        raise ResolveError("nothing at %s matched %s" % (url, list(patterns.values())))
    res = {"version": _version_from(files[0]["filename"], src), "date": None, "files": files}
    if missing:
        res["warnings"] = ["no file for " + ", ".join(missing)]
    return res


class _NoFollow(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def _redirect_target(url):
    """Where a "latest" link points, without visiting the place it points to."""
    req = urllib.request.Request(url, headers=_headers(url))
    try:
        urllib.request.build_opener(_NoFollow).open(req, timeout=60).close()
    except urllib.error.HTTPError as e:
        where = e.headers.get("Location")
        if e.code in (301, 302, 303, 307, 308) and where:
            return urllib.parse.urljoin(url, where)
        raise FetchError("HTTP %s for %s" % (e.code, url))
    except (urllib.error.URLError, OSError) as e:
        raise FetchError("%s for %s" % (e, url))
    raise FetchError("%s did not redirect" % url)


def r_redirect(src, net):
    """A "download the latest" link that redirects to a versioned folder.

    version_regex reads the version out of the folder address. files maps a platform to a file
    name with {version} in it. Each address is checked: one the server says is absent is dropped,
    one the server will not answer for is kept, since it follows the publisher's naming rule.
    """
    target = _redirect_target(src["url"])
    m = re.search(src["version_regex"], target)
    if not m:
        raise ResolveError("%s leads to %s, which does not look like a release" % (src["url"], target))
    version = m.group(1)
    base = target.rstrip("/") + "/"
    files, unsure = [], []
    for platform, template in src["files"].items():
        fn = template.replace("{version}", version)
        f = {"url": base + fn, "filename": fn}
        try:
            _, headers = net.head(f["url"])
            if str(headers.get("content-length", "")).isdigit():
                f["size"] = int(headers["content-length"])
        except FetchError as e:
            if "HTTP 404" in str(e):
                continue
            unsure.append(fn)
        if platform != "any":
            f["platform"] = platform
        files.append(f)
    if not files:
        raise ResolveError("none of the expected files exist under %s" % base)
    date = "%s-%s-%s" % (version[:4], version[4:6], version[6:]) if re.fullmatch(r"\d{8}", version) else None
    res = {"version": version, "date": date, "files": files}
    if unsure:
        res["warnings"] = ["the server would not confirm " + ", ".join(unsure)]
    return res


RESOLVERS = {
    "kiwix": r_kiwix, "checksum_file": r_checksum_file, "ubuntu": r_ubuntu, "openwrt": r_openwrt,
    "tails": r_tails, "rpi_imager": r_rpi_imager, "alpine": r_alpine, "fedora": r_fedora,
    "sourceforge": r_sourceforge, "fdroid": r_fdroid, "github": r_github, "toolchain": r_toolchain,
    "protomaps": r_protomaps, "ollama": r_ollama, "docker": r_docker, "static": r_static,
    "http_index": r_http_index, "redirect": r_redirect,
}
EXPANDERS = {"kiwix_rule": x_kiwix_rule, "ebible": x_ebible}


def resolve(item, net):
    """Return a list of (item, resolved) pairs for one catalog line."""
    src = item["source"]
    kind = src.get("type")
    try:
        if kind in EXPANDERS:
            # A rule line expands into many items; each carries the rule id it came from.
            return [(dict(item, rule=item["id"], **{k: v for k, v in over.items() if v is not None}), res)
                    for over, res in EXPANDERS[kind](src, net)]
        if kind not in RESOLVERS:
            raise ResolveError("unknown source type %r" % kind)
        res = RESOLVERS[kind](src, net)
        # Hints fill in what the catalog line left out (a Kiwix title, a language).
        return [(dict(res.pop("hint", {}), **{k: v for k, v in item.items() if v not in (None, "")}), res)]
    except ResolveError:
        raise
    except (FetchError, KeyError, IndexError, ValueError, StopIteration, AttributeError, TypeError) as e:
        raise ResolveError("%s: %s" % (type(e).__name__, e))
