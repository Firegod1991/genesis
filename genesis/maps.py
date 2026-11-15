"""Offline maps: cut any part of the planet out of the newest Protomaps build.

`pmtiles extract` reads only the byte ranges it needs from the remote planet
file, so a city or a country downloads in minutes without fetching 140 GB.
"""

import json
import os
import stat
import subprocess
import tarfile
import zipfile

from . import catalog
from .util import FetchError, Net, human, log


def _vault(args):
    from .fetch import vault_path
    return vault_path(args)


def planet_source(vault):
    """A local planet file if the vault has one, else the newest remote build."""
    local = os.path.join(vault, "maps", "planet.pmtiles")
    if os.path.exists(local):
        return local
    entry = catalog.load_lock("maps").get("planet_basemap", {})
    if not entry.get("files"):
        raise SystemExit("the catalog has no planet build yet: run `genesis refresh --pack maps`")
    return entry["files"][0]["url"]


def ensure_tool(vault):
    """Install the pmtiles binary for this machine into <vault>/bin and return its path."""
    exe = os.path.join(vault, "bin", "pmtiles.exe" if os.name == "nt" else "pmtiles")
    if os.path.exists(exe):
        return exe
    from .fetch import _check, _download
    entry = catalog.load_lock("tools").get("pmtiles", {})
    files = catalog.wanted_files(entry, [catalog.host_platform()])
    if not files:
        raise SystemExit("no pmtiles download for %s in the catalog. Get it from "
                         "https://github.com/protomaps/go-pmtiles/releases and put it at %s"
                         % (catalog.host_platform(), exe))
    f = files[0]
    archive = os.path.join(vault, "tools", f["filename"])
    if not os.path.exists(archive):
        log("get %s" % f["filename"])
        _download(f["url"], archive, f.get("size"))
    _check(archive, f, Net())
    os.makedirs(os.path.dirname(exe), exist_ok=True)
    name = os.path.basename(exe)
    if archive.endswith(".zip"):
        with zipfile.ZipFile(archive) as z, open(exe, "wb") as out:
            out.write(z.read(name))
    else:
        with tarfile.open(archive) as t, open(exe, "wb") as out:
            out.write(t.extractfile(name).read())
    os.chmod(exe, os.stat(exe).st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return exe


def poly_to_geojson(text):
    """Convert an Osmosis .poly boundary (as published by Geofabrik) to a GeoJSON MultiPolygon."""
    polygons, ring, hole = [], None, False
    for line in text.splitlines()[1:]:
        s = line.strip()
        if not s:
            continue
        if s == "END":
            if ring is not None:
                if ring and ring[0] != ring[-1]:
                    ring.append(ring[0])
                if hole and polygons:
                    polygons[-1].append(ring)
                elif not hole:
                    polygons.append([ring])
                ring = None
            continue
        parts = s.split()
        if ring is None:
            ring, hole = [], s.startswith("!")
        elif len(parts) >= 2:
            ring.append([float(parts[0]), float(parts[1])])
    if not polygons:
        raise FetchError("no rings found in .poly boundary")
    return {"type": "MultiPolygon", "coordinates": polygons}


def extract(vault, out_name, bbox=None, region=None, maxzoom=None):
    exe = ensure_tool(vault)
    out = os.path.join(vault, "maps", out_name + ".pmtiles")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    cmd = [exe, "extract", planet_source(vault), out + ".part"]
    if bbox:
        cmd.append("--bbox=" + bbox)
    if region:
        poly = Net().text("https://download.geofabrik.de/%s.poly" % region.strip("/"))
        boundary = os.path.join(vault, "maps", out_name + ".geojson")
        with open(boundary, "w", encoding="utf-8") as f:
            json.dump(poly_to_geojson(poly), f)
        cmd.append("--region=" + boundary)
    if maxzoom is not None:
        cmd.append("--maxzoom=%d" % maxzoom)
    log(" ".join(cmd))
    subprocess.check_call(cmd)
    os.replace(out + ".part", out)
    log("wrote %s (%s)" % (out, human(os.path.getsize(out))))
    return out


def list_maps(vault):
    folder = os.path.join(vault, "maps")
    found = []
    if os.path.isdir(folder):
        for name in sorted(os.listdir(folder)):
            if name.endswith(".pmtiles"):
                found.append({"name": name[:-8], "file": name, "bytes": os.path.getsize(os.path.join(folder, name))})
    return found


def main(args):
    vault = _vault(args)
    if args.action == "tool":
        print(ensure_tool(vault))
    elif args.action == "world":
        extract(vault, "world_overview", maxzoom=args.maxzoom if args.maxzoom is not None else 6)
    elif args.action == "region":
        if not (args.bbox or args.region):
            raise SystemExit("give --bbox west,south,east,north or --region <Geofabrik id such as us/california>")
        name = args.name or (args.region or "region").strip("/").replace("/", "_")
        extract(vault, name, bbox=args.bbox, region=args.region, maxzoom=args.maxzoom)
    elif args.action == "list":
        for m in list_maps(vault):
            print("%-28s %s" % (m["name"], human(m["bytes"])))
    return 0
