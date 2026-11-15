#!/usr/bin/env python3
"""Prove the published world data through Genesis itself, the way a new box meets it.

Run it after `genesis fetch --id survival_layers_world --id world_overview`. It asks real
questions in places spread over the planet and fails if an answer is missing, far off or slow.
The monthly refresh runs it, so a broken build cannot pass unnoticed.
"""

import json
import os
import socket
import subprocess
import sys
import time
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from genesis import poi  # noqa: E402
from genesis.fetch import vault_path  # noqa: E402

# Centres of large cities on every inhabited continent. In each of them the everyday layers
# must turn up within walking distance, whatever a month of map edits changes.
CITIES = [("Nairobi", -1.2864, 36.8172), ("Cairo", 30.0444, 31.2357), ("Sao Paulo", -23.5505, -46.6333),
          ("Mexico City", 19.4326, -99.1332), ("New York", 40.7580, -73.9855), ("Paris", 48.8566, 2.3522),
          ("Mumbai", 18.9388, 72.8354), ("Tokyo", 35.6812, 139.7671), ("Sydney", -33.8688, 151.2093)]
EVERYDAY = ("water", "medical", "fuel", "supply")
WITHIN_KM = 5.0
SLOW_SECONDS = 3.0
problems, lines = [], []


def say(text):
    print(text)
    lines.append(text)


def expect(ok, text):
    if not ok:
        problems.append(text)
        print("PROBLEM " + text)


def get(url, headers=None):
    started = time.time()
    with urllib.request.urlopen(urllib.request.Request(url, headers=headers or {}), timeout=60) as r:
        return r.status, r.read(), time.time() - started


def layers(db):
    totals = poi.stats(db)
    say("| Layer | Places |\n|---|---|")
    for cat in poi.categories():
        count = totals["categories"].get(cat, {}).get("total", 0)
        say("| %s | %s |" % (cat, format(count, ",")))
        expect(count >= 100000, "layer %s holds only %d places" % (cat, count))
    say("| **All** | **%s** |\n" % format(totals["total"], ","))
    expect(totals["total"] >= 5000000, "the database holds only %d places" % totals["total"])
    started = time.time()
    poi.stats(db)
    expect(time.time() - started < 1.0, "totals are counted again on every request instead of being kept")
    return totals["total"]


def searches(db):
    say("| City | " + " | ".join(EVERYDAY) + " | Search |\n|---|" + "---|" * (len(EVERYDAY) + 1))
    for name, lat, lon in CITIES:
        started = time.time()
        found = {cat: poi.nearest(db, lat, lon, cat, 2) for cat in poi.categories()}
        took = time.time() - started
        cells = []
        for cat in EVERYDAY:
            km = found[cat][0]["km"] if found[cat] else None
            cells.append("none" if km is None else "%.2f km" % km)
            expect(km is not None and km <= WITHIN_KM, "%s: nearest %s is %s" % (name, cat, cells[-1]))
        say("| %s | %s | %.0f ms |" % (name, " | ".join(cells), took * 1000))
        expect(took < SLOW_SECONDS, "%s: all layers took %.1f s" % (name, took))
    say("")
    # A search that starts beside the date line has to see both sides of it.
    fiji = poi.nearest(db, -16.80, 179.95, None, 5)
    expect(len(fiji) == 5 and fiji[-1]["km"] < 100, "nothing near Taveuni, Fiji, across the date line")
    # In the open ocean the right answer is far away or nothing, quickly.
    started = time.time()
    ocean = poi.nearest(db, 0.0, -140.0, "water", 3)
    expect(time.time() - started < SLOW_SECONDS, "an open ocean search took %.1f s" % (time.time() - started))
    expect(all(p["km"] > 500 for p in ocean), "drinking water reported in the middle of the Pacific")


def world_map(vault):
    path = os.path.join(vault, "maps", "world_overview.pmtiles")
    expect(os.path.exists(path), "world_overview.pmtiles is not in the vault")
    if os.path.exists(path):
        size = os.path.getsize(path)
        with open(path, "rb") as f:
            magic = f.read(8)
        expect(magic[:7] == b"PMTiles" and magic[7] == 3, "world_overview.pmtiles is not a PMTiles version 3 file")
        expect(size > 10000000, "world_overview.pmtiles is only %d bytes" % size)
        say("World map: %s MB, PMTiles version %s" % (format(size // 1000000, ","), magic[7] if len(magic) > 7 else "?"))


def dashboard(vault, total):
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    server = subprocess.Popen([sys.executable, os.path.join(ROOT, "dashboard", "server.py")],
                              env=dict(os.environ, GENESIS_VAULT=vault, PORT=str(port)),
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    base = "http://127.0.0.1:%d" % port
    try:
        for _ in range(50):
            try:
                get(base + "/api/system")
                break
            except OSError:
                time.sleep(0.2)
        status, body, took = get(base + "/api/poi/nearest?lat=48.8566&lon=2.3522&n=2&each=1")
        by = json.loads(body).get("by_category", {})
        expect(status == 200 and all(by.get(c) for c in poi.categories()), "dashboard: a layer is empty in central Paris")
        expect(took < SLOW_SECONDS, "dashboard: the bearing table took %.1f s" % took)
        say("Dashboard bearing table for Paris: %.0f ms" % (took * 1000))
        status, body, took = get(base + "/api/poi/stats")
        expect(json.loads(body).get("total") == total and took < SLOW_SECONDS, "dashboard: totals are wrong or slow (%.1f s)" % took)
        status, body, took = get(base + "/api/poi/bbox?west=2.25&south=48.81&east=2.42&north=48.90&cat=medical&limit=1500")
        places = json.loads(body).get("places", [])
        expect(len(places) > 100 and took < SLOW_SECONDS, "dashboard: the map layer for Paris returned %d places in %.1f s" % (len(places), took))
        status, body, took = get(base + "/api/maps")
        expect("world_overview" in [m["name"] for m in json.loads(body).get("maps", [])], "dashboard: the world map is not listed")
        status, body, took = get(base + "/maps/world_overview.pmtiles", {"Range": "bytes=0-16383"})
        expect(status == 206 and len(body) == 16384 and body[:7] == b"PMTiles", "dashboard: the map file is not served in ranges")
    finally:
        server.terminate()
        server.wait(timeout=10)


def main():
    vault = vault_path(type("Args", (), {"vault": sys.argv[1] if len(sys.argv) > 1 else None})())
    db = poi.connect(poi.default_db(vault))
    if db is None:
        print("PROBLEM no survival layers in %s: run `genesis fetch --id survival_layers_world` first" % vault)
        return 1
    built = ", ".join(sorted(poi.stats(db)["built"]))
    say("### World data check\n\nBuilt from: %s\n" % built)
    total = layers(db)
    searches(db)
    world_map(vault)
    dashboard(vault, total)
    say("\n**%s**" % ("All checks passed." if not problems else "%d problems:" % len(problems)))
    for p in problems:
        say("* " + p)
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
