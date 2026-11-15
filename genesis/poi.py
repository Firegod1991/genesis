"""Survival layers: where the water, care, fuel, power and shelter are.

Reads OpenStreetMap extracts with osmium, keeps the features listed in
catalog/poi_categories.json, and stores them as points in one SQLite file
that answers "what is nearest" with no network.
"""

import csv
import json
import math
import os
import shutil
import sqlite3
import subprocess
import sys

from .util import ROOT, FetchError, Net, file_hash, log, read_json

CATEGORY_FILE = os.path.join(ROOT, "catalog", "poi_categories.json")
KEEP_TAGS = ("operator", "opening_hours", "phone", "access", "fee", "capacity", "emergency", "ele",
             "seasonal", "drinking_water", "description", "healthcare:speciality", "beds",
             "plant:source", "plant:output:electricity", "generator:source", "generator:output:electricity",
             "voltage", "fuel:diesel", "fuel:lpg", "fuel:octane_95", "iata", "icao")
COMPASS = ("N", "NE", "E", "SE", "S", "SW", "W", "NW")


def categories():
    return read_json(CATEGORY_FILE)


def filter_expressions(cats=None):
    """osmium tags-filter expressions covering every category."""
    merged = {}
    for spec in (cats or categories()).values():
        for rule in spec["match"]:
            for key, values in rule.items():
                merged.setdefault(key, set()).update(values)
    return ["nwr/%s=%s" % (k, ",".join(sorted(v))) for k, v in sorted(merged.items())]


def classify(tags, cats):
    """Return (category, sub) for a tag dict, or None. The first category that matches wins."""
    for cat, spec in cats.items():
        for rule in spec["match"]:
            for key, values in rule.items():
                if tags.get(key) in values:
                    if key == "power" and tags[key] == "generator" and tags.get("generator:source") == "solar":
                        return None  # rooftop panels would bury everything else
                    sub = tags[key] if key != "drinking_water" else "drinking_water"
                    return cat, sub
    return None


def _point(geom):
    kind, c = geom.get("type"), geom.get("coordinates")
    try:
        if kind == "Point":
            return c[0], c[1]
        pts = {"LineString": lambda: c, "Polygon": lambda: c[0], "MultiPolygon": lambda: c[0][0],
               "MultiLineString": lambda: c[0]}[kind]()
        xs, ys = [p[0] for p in pts], [p[1] for p in pts]
        return (min(xs) + max(xs)) / 2.0, (min(ys) + max(ys)) / 2.0
    except (KeyError, IndexError, TypeError):
        return None


def default_db(vault):
    return os.path.join(vault, "poi", "survival.sqlite")


def connect(path, create=False):
    if not create and not os.path.exists(path):
        return None
    os.makedirs(os.path.dirname(path), exist_ok=True)
    db = sqlite3.connect(path)
    if create:
        db.executescript("""
            CREATE TABLE IF NOT EXISTS poi(id INTEGER PRIMARY KEY, cat TEXT NOT NULL, sub TEXT, name TEXT,
                lat REAL NOT NULL, lon REAL NOT NULL, osm TEXT UNIQUE, tags TEXT);
            CREATE INDEX IF NOT EXISTS poi_cat ON poi(cat);
            CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT);""")
        try:
            db.execute("CREATE VIRTUAL TABLE IF NOT EXISTS poi_rt USING rtree(id, min_lon, max_lon, min_lat, max_lat)")
        except sqlite3.OperationalError:  # SQLite built without R*Tree: plain indexes still work
            db.execute("CREATE INDEX IF NOT EXISTS poi_lat ON poi(lat)")
            db.execute("CREATE INDEX IF NOT EXISTS poi_lon ON poi(lon)")
    return db


def _has_rtree(db):
    return db.execute("SELECT 1 FROM sqlite_master WHERE name='poi_rt'").fetchone() is not None


def _insert(db, rtree, cat, sub, name, lat, lon, osm, tags):
    cur = db.execute("INSERT OR IGNORE INTO poi(cat, sub, name, lat, lon, osm, tags) VALUES (?,?,?,?,?,?,?)",
                     (cat, sub, name, lat, lon, osm, json.dumps(tags, ensure_ascii=False) if tags else None))
    if cur.rowcount == 1 and rtree:
        db.execute("INSERT INTO poi_rt VALUES (?,?,?,?,?)", (cur.lastrowid, lon, lon, lat, lat))
    return cur.rowcount


def ingest_geojsonseq(db, lines, cats=None):
    """Load osmium export output (one GeoJSON feature per line). Returns rows added."""
    cats = cats or categories()
    rtree, added = _has_rtree(db), 0
    for line in lines:
        line = line.strip().lstrip("\x1e")
        if not line:
            continue
        try:
            feat = json.loads(line)
        except ValueError:
            continue
        props = feat.get("properties") or {}
        hit = classify(props, cats)
        pt = _point(feat.get("geometry") or {})
        if not hit or not pt or not (-180 <= pt[0] <= 180 and -90 <= pt[1] <= 90):
            continue
        osm = "%s%s" % (str(props.get("@type", "x"))[:1], props.get("@id", ""))
        tags = {k: props[k] for k in KEEP_TAGS if props.get(k)}
        name = props.get("name:en") or props.get("name")
        added += _insert(db, rtree, hit[0], hit[1], name, round(pt[1], 6), round(pt[0], 6), osm, tags)
    db.commit()
    return added


def ingest_power_csv(db, path):
    """Load the WRI Global Power Plant Database: fuel type and capacity for plants worldwide."""
    rtree, added = _has_rtree(db), 0
    with open(path, encoding="utf-8", newline="") as f:
        for r in csv.DictReader(f):
            try:
                lat, lon = float(r["latitude"]), float(r["longitude"])
            except (KeyError, ValueError):
                continue
            tags = {"plant:source": (r.get("primary_fuel") or "").lower(), "capacity_mw": r.get("capacity_mw"),
                    "operator": r.get("owner"), "country": r.get("country"), "source": "WRI GPPD"}
            added += _insert(db, rtree, "power", "plant", r.get("name"), lat, lon,
                             "wri:" + (r.get("gppd_idnr") or r.get("name", "")), {k: v for k, v in tags.items() if v})
    db.commit()
    return added


def build(pbf, db_path, replace=False, label=None):
    """Filter an OSM extract with osmium and load the survival layers into the database."""
    osmium = shutil.which("osmium")
    if not osmium:
        raise SystemExit("osmium is required: sudo apt install osmium-tool (or brew install osmium-tool)")
    if replace and os.path.exists(db_path):
        os.remove(db_path)
    work = os.path.join(os.path.dirname(db_path), ".work")
    os.makedirs(work, exist_ok=True)
    filtered = os.path.join(work, "filtered.osm.pbf")
    seq = os.path.join(work, "filtered.geojsonseq")
    try:
        log("filtering %s ..." % pbf)
        subprocess.check_call([osmium, "tags-filter", pbf] + filter_expressions() + ["-o", filtered, "--overwrite"])
        log("exporting geometry ...")
        subprocess.check_call([osmium, "export", filtered, "-f", "geojsonseq", "-x", "print_record_separator=false",
                               "-a", "type,id", "-o", seq, "--overwrite"])
        db = connect(db_path, create=True)
        with open(seq, encoding="utf-8") as f:
            added = ingest_geojsonseq(db, f)
        db.execute("INSERT OR REPLACE INTO meta VALUES (?, datetime('now'))", ("built:" + (label or os.path.basename(pbf)),))
        db.commit()
        stats(db)  # counted once here, so the dashboard never has to: it sees the vault read only
        db.close()
    finally:
        shutil.rmtree(work, ignore_errors=True)
    log("added %d places to %s" % (added, db_path))
    return added


def merge(db_path, sources):
    """Fold other survival layer databases into one. Places already present are skipped."""
    db = connect(db_path, create=True)
    rtree, added = _has_rtree(db), 0
    for src_path in sources:
        src = sqlite3.connect(src_path)
        for cat, sub, name, lat, lon, osm, tags in src.execute("SELECT cat, sub, name, lat, lon, osm, tags FROM poi"):
            added += _insert(db, rtree, cat, sub, name, lat, lon, osm, json.loads(tags) if tags else None)
        for key, value in src.execute("SELECT key, value FROM meta"):
            db.execute("INSERT OR REPLACE INTO meta VALUES (?, ?)", (key, value))
        src.close()
        db.commit()
        log("merged %s" % src_path)
    stats(db)
    db.execute("VACUUM")
    db.close()
    return added


def fetch_region(region, vault):
    """Download a Geofabrik extract (for example europe/monaco) and verify its MD5."""
    from .fetch import _download
    url = "https://download.geofabrik.de/%s-latest.osm.pbf" % region.strip("/")
    dest = os.path.join(vault, "osm", region.strip("/").replace("/", "_") + "-latest.osm.pbf")
    log("downloading %s" % url)
    _download(url, dest)
    want = Net().text(url + ".md5").split()[0].lower()
    if file_hash(dest, "md5") != want:
        os.remove(dest)
        raise FetchError("MD5 mismatch for %s, removed it. Run again." % dest)
    return dest


# ------------------------------------------------------------------ queries

def _distance(lat1, lon1, lat2, lon2):
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dl = math.radians(lon2 - lon1)
    a = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    km = 6371.0088 * 2 * math.asin(min(1.0, math.sqrt(a)))
    y = math.sin(dl) * math.cos(p2)
    x = math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(dl)
    bearing = (math.degrees(math.atan2(y, x)) + 360) % 360
    return km, bearing


def _row(r):
    d = {"id": r[0], "cat": r[1], "sub": r[2], "name": r[3], "lat": r[4], "lon": r[5], "osm": r[6]}
    d["tags"] = json.loads(r[7]) if r[7] else {}
    return d


def _boxes(west, south, east, north):
    """A box as one or two longitude ranges, so a search across the date line finds both sides.

    Each range comes with the shift that puts the searcher's longitude on its side of the line.
    """
    south, north = max(-90.0, south), min(90.0, north)
    if east - west >= 360:
        return [(-180.0, south, 180.0, north, 0.0)]
    if west < -180:
        return [(-180.0, south, east, north, 0.0), (west + 360, south, 180.0, north, 360.0)]
    if east > 180:
        return [(west, south, 180.0, north, 0.0), (-180.0, south, east - 360, north, -360.0)]
    return [(west, south, east, north, 0.0)]


def _select(db, box, cat=None, tail="", extra=()):
    """Rows inside a box, read through the spatial index.

    CROSS JOIN and the plus sign pin the plan. Left to itself SQLite starts from the category
    index, which on the world database reads a million rows to return a handful.
    """
    cols = "p.id, p.cat, p.sub, p.name, p.lat, p.lon, p.osm, p.tags"
    west, south, east, north = box[:4]
    args = [west, east, south, north]
    if _has_rtree(db):
        sql = ("SELECT %s FROM poi_rt r CROSS JOIN poi p ON p.id = r.id "
               "WHERE r.min_lon >= ? AND r.max_lon <= ? AND r.min_lat >= ? AND r.max_lat <= ?" % cols)
    else:
        sql = "SELECT %s FROM poi p WHERE p.lon >= ? AND p.lon <= ? AND p.lat >= ? AND p.lat <= ?" % cols
    if cat:
        cats = cat.split(",")
        sql += " AND +p.cat IN (%s)" % ",".join("?" * len(cats))
        args += cats
    return db.execute(sql + tail, args + list(extra))


def in_bbox(db, west, south, east, north, cat=None, limit=2000):
    rows = []
    for box in _boxes(west, south, east, north):
        if len(rows) < limit:
            rows += [_row(r) for r in _select(db, box, cat, " LIMIT ?", [limit - len(rows)])]
    return rows


def nearest(db, lat, lon, cat=None, n=5, max_km=2000):
    """Closest places by great circle distance, searching outward until n are found."""
    coslat = max(0.02, math.cos(math.radians(lat)))
    # A flat earth estimate in SQL keeps only the closest candidates of a busy box, so a search
    # that reaches a city never hauls the whole city into memory. Exact distance decides after.
    closest = " ORDER BY (p.lat - ?) * (p.lat - ?) + (p.lon - ?) * (p.lon - ?) * ? LIMIT ?"
    keep = max(64, 8 * n)
    radius = 2.0
    while True:
        dlat = radius / 111.0
        dlon = min(180.0, radius / (111.0 * coslat))
        found = []
        for box in _boxes(lon - dlon, lat - dlat, lon + dlon, lat + dlat):
            here = lon + box[4]
            for r in _select(db, box, cat, closest, [lat, lat, here, here, coslat * coslat, keep]):
                r = _row(r)
                km, bearing = _distance(lat, lon, r["lat"], r["lon"])
                # Only the circle inside the box is complete: a corner can hide a nearer place outside it.
                if km <= radius:
                    r["km"] = round(km, 2)
                    r["bearing"] = round(bearing)
                    r["compass"] = COMPASS[int((bearing + 22.5) // 45) % 8]
                    found.append(r)
        if len(found) >= n or radius >= max_km:
            return sorted(found, key=lambda r: r["km"])[:n]
        radius = min(float(max_km), radius * 2)


def _count(db):
    out = {"total": 0, "categories": {}}
    for cat, sub, count in db.execute("SELECT cat, sub, count(*) FROM poi GROUP BY cat, sub ORDER BY cat, count(*) DESC"):
        c = out["categories"].setdefault(cat, {"total": 0, "kinds": {}})
        c["total"] += count
        c["kinds"][sub] = count
        out["total"] += count
    return out


def stats(db):
    """Totals by layer and kind, and what the database was built from.

    Counting the world takes seconds, so the totals are kept in the database and counted again
    only after places were added.
    """
    newest = db.execute("SELECT max(id) FROM poi").fetchone()[0] or 0
    out = None
    kept = db.execute("SELECT value FROM meta WHERE key = 'stats'").fetchone()
    if kept:
        try:
            kept = json.loads(kept[0])
            if kept["newest"] == newest:
                out = {"total": kept["total"], "categories": kept["categories"]}
        except (ValueError, KeyError, TypeError):
            pass
    if out is None:
        out = _count(db)
        try:
            db.execute("INSERT OR REPLACE INTO meta VALUES ('stats', ?)", (json.dumps(dict(out, newest=newest)),))
            db.commit()
        except sqlite3.OperationalError:  # a vault on read only media: the count stands, it is just not kept
            pass
    out["built"] = {}
    for key, value in db.execute("SELECT key, value FROM meta WHERE key LIKE 'built:%'"):
        out["built"][key.replace("built:", "")] = value
    return out


def export(db, fmt, cat=None, out=sys.stdout):
    sql, args = "SELECT id, cat, sub, name, lat, lon, osm, tags FROM poi", []
    if cat:
        sql, args = sql + " WHERE cat = ?", [cat]
    if fmt == "csv":
        w = csv.writer(out)
        w.writerow(["cat", "kind", "name", "lat", "lon", "osm", "tags"])
        for r in db.execute(sql, args):
            w.writerow([r[1], r[2], r[3] or "", r[4], r[5], r[6], r[7] or ""])
    else:
        out.write('{"type":"FeatureCollection","features":[\n')
        first = True
        for r in db.execute(sql, args):
            d = _row(r)
            feat = {"type": "Feature", "geometry": {"type": "Point", "coordinates": [d["lon"], d["lat"]]},
                    "properties": dict(d["tags"], cat=d["cat"], kind=d["sub"], name=d["name"], osm=d["osm"])}
            out.write(("" if first else ",\n") + json.dumps(feat, ensure_ascii=False))
            first = False
        out.write("\n]}\n")


def main(args):
    from .fetch import vault_path
    vault = vault_path(args)
    db_path = args.db or default_db(vault)
    if args.action == "categories":
        for cat, spec in categories().items():
            kinds = sorted({v for rule in spec["match"] for vals in rule.values() for v in vals})
            print("%-10s %s: %s" % (cat, spec["label"], ", ".join(kinds)))
        return 0
    if args.action == "merge":
        if not args.source:
            raise SystemExit("give one or more --source FILE databases to merge into %s" % db_path)
        print("added %d places" % merge(db_path, args.source))
        return 0
    if args.action == "build":
        pbf = args.pbf or (fetch_region(args.region, vault) if args.region else None)
        if not pbf:
            raise SystemExit("give --pbf FILE or --region NAME (a Geofabrik id such as europe/monaco)")
        build(pbf, db_path, replace=args.replace, label=args.region)
        power = os.path.join(vault, "poi", "sources", "global_power_plant_database.csv")
        if os.path.exists(power):
            db = connect(db_path)
            if not db.execute("SELECT 1 FROM meta WHERE key='built:wri_power_plants'").fetchone():
                n = ingest_power_csv(db, power)
                db.execute("INSERT OR REPLACE INTO meta VALUES ('built:wri_power_plants', datetime('now'))")
                db.commit()
                stats(db)
                log("added %d power plants from the WRI database" % n)
        return 0
    db = connect(db_path)
    if db is None:
        raise SystemExit("no survival layers yet at %s. Run `genesis fetch --id survival_layers_world` "
                         "or `genesis poi build --region <name>`." % db_path)
    if args.action == "stats":
        print(json.dumps(stats(db), indent=1))
    elif args.action == "nearest":
        if args.lat is None or args.lon is None:
            raise SystemExit("give --lat and --lon")
        for r in nearest(db, args.lat, args.lon, args.cat, args.n):
            print("%7.2f km %-2s  %-9s %-16s %s  (%.5f, %.5f)" % (
                r["km"], r["compass"], r["cat"], r["sub"], r["name"] or "unnamed", r["lat"], r["lon"]))
    elif args.action == "export":
        if args.out:
            with open(args.out, "w", encoding="utf-8", newline="") as f:
                export(db, args.format, args.cat, f)
        else:
            export(db, args.format, args.cat)
    return 0
