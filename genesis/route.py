"""Find a way along the roads and paths in the maps on the drive, with no internet and no routing server.

The road layer of the map's own tiles is the network: every road, street, track, footpath and set of steps
the map draws. Genesis reads the tiles between two points, joins the roads where they meet, and finds the
shortest way on foot or the quickest by car. A way that leaves the roads (the last stretch to a spring in a
field) is shown as a straight line to walk by compass.
"""

import heapq
import math

from . import pmtiles

# What each way of travelling may use, and how fast it goes there in km/h.
WALK = {"highway": None, "major_road": 4.8, "medium_road": 4.8, "minor_road": 4.8, "path": 4.8, "other": 4.0}
DRIVE = {"highway": 100, "major_road": 60, "medium_road": 50, "minor_road": 30, "path": None, "other": None}
SLOW = {"service": 15, "driveway": 10, "track": 15, "steps": 2.4, "raceway": None, "runway": None,
        "taxiway": None, "platform": None, "corridor": 4.0, "proposed": None, "construction": None}
MODES = {"walk": WALK, "drive": DRIVE}
TOO_FAR = 30000  # metres in a straight line: beyond this, walk the bearing or use the paper map
EARTH = 6371008.8


def metres(lon1, lat1, lon2, lat2):
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH * math.asin(min(1.0, math.sqrt(h)))


def bearing(lon1, lat1, lon2, lat2):
    p1, p2, dl = math.radians(lat1), math.radians(lat2), math.radians(lon2 - lon1)
    x = math.sin(dl) * math.cos(p2)
    y = math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(dl)
    return (math.degrees(math.atan2(x, y)) + 360) % 360


def speed(mode, props):
    """km/h on this road for this way of travelling, or None where it may not go."""
    kind, detail = props.get("kind"), props.get("kind_detail")
    if kind in ("rail", "ferry", "aerialway") or detail == "ferry":
        return None
    base = MODES[mode].get(kind, MODES[mode].get("other"))
    if base is None:
        return None
    if detail in SLOW:
        slow = SLOW[detail]
        if slow is None:
            return None
        return min(base, slow) if mode == "drive" else (slow if detail in ("steps", "corridor") else base)
    if mode == "drive" and props.get("is_link"):
        return min(base, 50)
    return base


class Network:
    """The roads read from a set of tiles, joined where they meet.

    Tiles keep the shape of a road and drop points it does not need to draw it, so a side street often
    ends a step short of the road it joins, with no point in common. Every loose end that stops within a
    few metres of another road is joined to it before a way is looked for."""

    REACH = 7.0  # metres: a loose end this close to another road meets it

    def __init__(self, zoom):
        self.zoom, self.size = zoom, None
        self.segments = {}  # (a, b) with a < b: [km/h, one way (from a to b, from b to a or both), name]
        self.edges = {}     # node: [(next node, seconds, metres, name)], made by join()

    def add_tile(self, data, tx, ty, mode):
        for props, lines, extent in pmtiles.layer_lines(data, "roads"):
            kmh = speed(mode, props)
            if not kmh:
                continue
            self.size = extent << self.zoom
            ox, oy = tx * extent, ty * extent
            oneway = mode == "drive" and props.get("oneway") in ("yes", "-1")
            backwards = props.get("oneway") == "-1"
            name = props.get("name:en") or props.get("name") or props.get("ref") or ""
            for line in lines:
                prev = None
                for x, y in line:
                    node = (ox + x, oy + y)
                    if prev is not None and node != prev:
                        a, b = (prev, node) if not backwards else (node, prev)
                        self._segment(a, b, kmh, oneway, name)
                    prev = node

    def _segment(self, a, b, kmh, oneway, name):
        key, way = ((a, b), "ab") if a < b else ((b, a), "ba")
        way = way if oneway else "both"
        seen = self.segments.get(key)
        if seen is None:
            self.segments[key] = [kmh, way, name]
        else:  # the same stretch from the tile next door, or two roads drawn on one line: the freer wins
            if seen[1] != way:
                seen[1] = "both"
            seen[0] = max(seen[0], kmh)
            seen[2] = seen[2] or name

    def lonlat(self, node):
        return pmtiles.unit_to_lonlat(node[0], node[1], self.size)

    def join(self):
        """Join the loose ends to the roads they stop short of, then make the edges a way is found along."""
        if not self.segments:
            return
        first = next(iter(self.segments))[0]
        here, beside = self.lonlat(first), self.lonlat((first[0] + 1, first[1]))
        unit = metres(here[0], here[1], beside[0], beside[1])  # metres in one tile unit, here
        reach = self.REACH / max(unit, 1e-6)
        cell = max(16, int(reach * 4))
        degree, grid = {}, {}
        segs = list(self.segments.items())
        for i, ((a, b), _) in enumerate(segs):
            degree[a] = degree.get(a, 0) + 1
            degree[b] = degree.get(b, 0) + 1
            for cx in range(int(min(a[0], b[0]) // cell), int(max(a[0], b[0]) // cell) + 1):
                for cy in range(int(min(a[1], b[1]) // cell), int(max(a[1], b[1]) // cell) + 1):
                    grid.setdefault((cx, cy), []).append(i)
        splits, links = {}, []
        for node, d in degree.items():
            if d != 1:
                continue
            best, far = None, reach
            cx, cy = int(node[0] // cell), int(node[1] // cell)
            for gx in (cx - 1, cx, cx + 1):
                for gy in (cy - 1, cy, cy + 1):
                    for i in grid.get((gx, gy), ()):
                        (a, b), _ = segs[i]
                        if node in (a, b):
                            continue
                        vx, vy = b[0] - a[0], b[1] - a[1]
                        ll = vx * vx + vy * vy
                        t = max(0.0, min(1.0, ((node[0] - a[0]) * vx + (node[1] - a[1]) * vy) / ll))
                        px, py = a[0] + t * vx, a[1] + t * vy
                        dist = math.hypot(node[0] - px, node[1] - py)
                        if dist < far:
                            best, far = (i, t, (int(round(px)), int(round(py)))), dist
            if best is None:
                continue
            i, t, point = best
            (a, b), _ = segs[i]
            if point in (a, b) or t <= 0.0 or t >= 1.0:
                target = a if t < 0.5 else b
            else:
                splits.setdefault(i, []).append((t, point))
                target = point
            if target != node:
                links.append((node, target, segs[i][1]))
        for i, cuts in splits.items():
            (a, b), (kmh, way, name) = segs[i]
            del self.segments[(a, b)]
            chain = [a] + [p for _, p in sorted(cuts)] + [b]
            for p, q in zip(chain, chain[1:]):
                if p != q:
                    oneway = way != "both"
                    self._segment(p, q, kmh, oneway, name) if way != "ba" else self._segment(q, p, kmh, oneway, name)
        for node, target, (kmh, way, name) in links:
            self._segment(node, target, kmh, False, name)
        self.edges = {}
        for (a, b), (kmh, way, name) in self.segments.items():
            pa, pb = self.lonlat(a), self.lonlat(b)
            m = metres(pa[0], pa[1], pb[0], pb[1])
            s = m / (kmh / 3.6)
            self.edges.setdefault(a, [])
            self.edges.setdefault(b, [])
            if way in ("both", "ab"):
                self.edges[a].append((b, s, m, name))
            if way in ("both", "ba"):
                self.edges[b].append((a, s, m, name))

    def biggest_part(self):
        """The nodes of the largest piece of the network that hangs together."""
        seen, best = set(), set()
        back = {}
        for a, out in self.edges.items():
            for b, _, _, _ in out:
                back.setdefault(b, []).append(a)
        for start in self.edges:
            if start in seen:
                continue
            part, todo = {start}, [start]
            seen.add(start)
            while todo:
                n = todo.pop()
                for nxt in [e[0] for e in self.edges.get(n, ())] + back.get(n, []):
                    if nxt not in seen:
                        seen.add(nxt)
                        part.add(nxt)
                        todo.append(nxt)
            if len(part) > len(best):
                best = part
        return best

    def nearest(self, lon, lat, among):
        best, far = None, float("inf")
        cos = math.cos(math.radians(lat))
        for node in among:
            nlon, nlat = self.lonlat(node)
            d = ((nlon - lon) * cos) ** 2 + (nlat - lat) ** 2
            if d < far:
                best, far = node, d
        return best

    def shortest(self, start, goal, fastest_kmh):
        gl = self.lonlat(goal)

        def guess(n):
            p = self.lonlat(n)
            return metres(p[0], p[1], gl[0], gl[1]) / (fastest_kmh / 3.6)
        queue, cost, came = [(guess(start), 0.0, start)], {start: 0.0}, {start: None}
        while queue:
            _, spent, node = heapq.heappop(queue)
            if node == goal:
                break
            if spent > cost.get(node, float("inf")):
                continue
            for nxt, s, m, name in self.edges.get(node, ()):
                c = spent + s
                if c < cost.get(nxt, float("inf")):
                    cost[nxt], came[nxt] = c, (node, s, m, name)
                    heapq.heappush(queue, (c + guess(nxt), c, nxt))
        if goal not in came:
            return None
        legs, node = [], goal
        while came[node]:
            prev, s, m, name = came[node]
            legs.append((prev, node, s, m, name))
            node = prev
        legs.reverse()
        return legs


def turn(before, after):
    change = (after - before + 540) % 360 - 180
    if abs(change) < 25:
        return "continue"
    if abs(change) > 155:
        return "make a U turn"
    side = "right" if change > 0 else "left"
    return ("bear " if abs(change) < 60 else "turn ") + side


def _heading(points, from_end=False, span=25.0):
    """The bearing along the first (or, from_end, the last) span metres of a list of points."""
    pts = points[::-1] if from_end else points
    a, b, prev, gone = pts[0], pts[-1], pts[0], 0.0
    for p in pts[1:]:
        gone += metres(prev[0], prev[1], p[0], p[1])
        prev = p
        if gone >= span:
            b = p
            break
    h = bearing(a[0], a[1], b[0], b[1])
    return (h + 180) % 360 if from_end else h


def steps(net, legs):
    """The way in words: one line for each road followed, with the turn onto it. Short unnamed pieces,
    a crossing or a bit of pavement, are folded into the road before them."""
    runs = []
    for a, b, s, m, name in legs:
        if runs and (runs[-1]["name"] == name or (not name and m < 40 and runs[-1]["short"] + m < 40)):
            run = runs[-1]
            run["short"] = run["short"] + m if not name else 0
        else:
            run = {"name": name, "metres": 0.0, "seconds": 0.0, "points": [net.lonlat(a)], "short": 0.0}
            runs.append(run)
        run["metres"] += m
        run["seconds"] += s
        run["points"].append(net.lonlat(b))
    merged = []
    for run in runs:  # an unnamed piece folded away may leave the same road on both sides of it
        if merged and merged[-1]["name"] == run["name"]:
            merged[-1]["metres"] += run["metres"]
            merged[-1]["seconds"] += run["seconds"]
            merged[-1]["points"] += run["points"][1:]
        else:
            merged.append(run)
    words = []
    for i, run in enumerate(merged):
        road = run["name"] or "the unnamed way"
        if i == 0:
            how = "Head %s on %s" % (compass(_heading(run["points"])), road)
        else:
            how = "%s onto %s" % (turn(_heading(merged[i - 1]["points"], True), _heading(run["points"])).capitalize(), road)
        words.append({"text": how, "metres": round(run["metres"]), "seconds": round(run["seconds"])})
    return words


def compass(deg):
    return ("north", "north east", "east", "south east", "south", "south west", "west", "north west")[int((deg + 22.5) // 45) % 8]


def tiles_between(a, b, zoom, margin):
    """The tiles at this zoom that cover both points and a margin around them, in metres."""
    lat = (a[1] + b[1]) / 2
    dlat = margin / 111320.0
    dlon = margin / (111320.0 * max(0.1, math.cos(math.radians(lat))))
    west, east = min(a[0], b[0]) - dlon, max(a[0], b[0]) + dlon
    south, north = min(a[1], b[1]) - dlat, max(a[1], b[1]) + dlat
    x0, y0 = pmtiles.lonlat_to_tile(west, north, zoom)
    x1, y1 = pmtiles.lonlat_to_tile(east, south, zoom)
    return [(x, y) for x in range(x0, x1 + 1) for y in range(y0, y1 + 1)]


def route(archive, start, goal, mode="walk", max_tiles=400):
    """The way from start to goal, both (lon, lat), on foot or by car, as a dict ready to send to a page."""
    if mode not in MODES:
        raise ValueError("mode is walk or drive")
    straight = metres(start[0], start[1], goal[0], goal[1])
    if straight > TOO_FAR:
        return {"found": False, "why": "Too far for a route from the map: %.0f km in a straight line. "
                                      "Walk the bearing, or follow the paper map." % (straight / 1000)}
    if archive.maxzoom < 13:
        return {"found": False, "why": "This map has no street detail here. Cut a region with: genesis maps region"}
    zoom = min(14, archive.maxzoom)
    fastest = max(v for v in MODES[mode].values() if v)
    for margin in (max(800.0, straight * 0.4), max(2500.0, straight * 1.0)):
        cells = tiles_between(start, goal, zoom, margin)
        if len(cells) > max_tiles:
            break
        net = Network(zoom)
        for tx, ty in cells:
            data = archive.tile(zoom, tx, ty)
            if data:
                net.add_tile(data, tx, ty, mode)
        net.join()
        if not net.edges:
            return {"found": False, "why": "The map has no roads or paths here."}
        part = net.biggest_part()
        a, b = net.nearest(start[0], start[1], part), net.nearest(goal[0], goal[1], part)
        legs = net.shortest(a, b, fastest)
        if legs is None:
            continue
        line = [list(start)] + [list(net.lonlat(a))] + [list(net.lonlat(leg[1])) for leg in legs] + [list(goal)]
        pa, pb = net.lonlat(a), net.lonlat(b)
        off_start, off_goal = metres(start[0], start[1], pa[0], pa[1]), metres(goal[0], goal[1], pb[0], pb[1])
        on_road = sum(leg[3] for leg in legs)
        walk_off = (off_start + off_goal) / (4.8 / 3.6)
        return {"found": True, "mode": mode, "metres": round(on_road + off_start + off_goal),
                "seconds": round(sum(leg[2] for leg in legs) + walk_off),
                "straight": round(straight), "off_road": [round(off_start), round(off_goal)],
                "line": [[round(p[0], 6), round(p[1], 6)] for p in line], "steps": steps(net, legs)}
    return {"found": False, "why": "No way along the roads on this map joins these two places. Walk the bearing."}
