"""A way along the roads of the map on the drive: tiles read from a PMTiles file, roads joined where they
meet and where a tile left a side street a step short, one way streets kept by car and not on foot."""

import json
import os
import shutil
import sys
import tempfile
import threading
import unittest
import urllib.request
from http.server import ThreadingHTTPServer
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "dashboard"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import fake_tiles  # noqa: E402
from genesis import pmtiles, route  # noqa: E402

Z = 14
TX, TY = pmtiles.lonlat_to_tile(7.42, 43.73, Z)
X0, Y0 = TX * 4096 + 3500, TY * 4096 + 3500  # near a corner, so the streets run into the tiles beside
STEP = 400  # about 170 m here


def at(col, row):
    return X0 + col * STEP, Y0 + row * STEP


def town(oneway_top=False, footpath=False, spur_gap=None):
    """Five streets each way, crossing at shared points, over four tiles."""
    lines = []
    for r in range(5):
        pts = [at(c, r) for c in range(5)]
        props = {"kind": "minor_road", "kind_detail": "residential", "name": "Row %d" % r}
        if r == 0 and oneway_top:  # one way, east to west
            pts, props = pts[::-1], dict(props, oneway="yes")
        lines.append((props, pts))
    for c in range(5):
        lines.append(({"kind": "minor_road", "name": "Column %d" % c}, [at(c, r) for r in range(5)]))
    lines.append(({"kind": "rail", "kind_detail": "rail"}, [at(0, 0), at(4, 4)]))  # never a way for anyone
    if footpath:  # straight across the town, for feet only
        lines.append(({"kind": "path", "kind_detail": "footway"}, [at(0, 4), at(4, 0)]))
    if spur_gap is not None:  # a lane that stops short of Row 2, between two crossings
        x, y = X0 + STEP * 2 + STEP // 2, Y0 + STEP * 2 - spur_gap
        lines.append(({"kind": "minor_road", "name": "Spur Lane"}, [(x, y - 600), (x, y)]))
    return fake_tiles.streets(lines, Z)


def place(col, row):
    return fake_tiles.lonlat(*at(col, row), Z)


class RouteTests(unittest.TestCase):
    def setUp(self):
        self.top = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.top, ignore_errors=True)

    def archive(self, tiles, leaf=False):
        path = os.path.join(self.top, "town%d.pmtiles" % len(os.listdir(self.top)))
        fake_tiles.write(path, tiles, Z, bounds=(7.3, 43.6, 7.6, 43.9), leaf=leaf)
        return pmtiles.Archive(path)

    def test_tiles_come_out_as_they_went_in_through_a_leaf_directory_too(self):
        tiles = town()
        self.assertGreater(len(tiles), 1, "the town crosses into the tiles beside")
        for leaf in (False, True):
            arc = self.archive(tiles, leaf=leaf)
            for (x, y), data in tiles.items():
                self.assertEqual(arc.tile(Z, x, y), data)
            self.assertIsNone(arc.tile(Z, TX + 9, TY + 9))
            self.assertIsNone(arc.tile(Z - 1, TX // 2, TY // 2), "a zoom the map does not carry")
        props, lines, extent = next(pmtiles.layer_lines(tiles[(TX, TY)], "roads"))
        self.assertEqual((props, extent), ({"kind": "minor_road", "kind_detail": "residential", "name": "Row 0"}, 4096))
        self.assertEqual(lines, [[(3500, 3500), (3900, 3500), (4300, 3500), (4700, 3500), (5100, 3500)]])
        self.assertEqual([pmtiles.zxy_to_tileid(1, x, y) for x, y in ((0, 0), (0, 1), (1, 1), (1, 0))], [1, 2, 3, 4])

    def test_the_way_across_town_follows_the_streets_and_crosses_tiles(self):
        arc = self.archive(town())
        r = route.route(arc, place(0, 0), place(4, 4), "walk")
        self.assertTrue(r["found"], r)
        self.assertAlmostEqual(r["metres"] / r["straight"], 2 ** 0.5, delta=0.05, msg="eight blocks, never the railway")
        self.assertLess(max(r["off_road"]), 2)
        self.assertEqual(r["line"][0], [round(v, 6) for v in place(0, 0)])
        self.assertEqual(r["steps"][0]["text"].split(" on ")[0], "Head east" if "Row" in r["steps"][0]["text"] else "Head south")
        self.assertTrue(all(s["text"].split(" onto ")[0] in ("Turn left", "Turn right") for s in r["steps"][1:]), r["steps"])
        self.assertAlmostEqual(r["seconds"], r["metres"] / (4.8 / 3.6), delta=5)

    def test_a_footpath_is_for_feet_and_a_one_way_street_for_cars_one_way(self):
        arc = self.archive(town(oneway_top=True, footpath=True))
        walk = route.route(arc, place(0, 4), place(4, 0), "walk")
        self.assertAlmostEqual(walk["metres"], walk["straight"], delta=5, msg="straight down the footpath")
        drive = route.route(arc, place(0, 4), place(4, 0), "drive")
        self.assertGreater(drive["metres"], walk["metres"] * 1.3, "no footpaths by car")
        east = route.route(arc, place(0, 0), place(4, 0), "drive")
        west = route.route(arc, place(4, 0), place(0, 0), "drive")
        self.assertAlmostEqual(west["metres"], west["straight"], delta=5, msg="with the one way street")
        self.assertGreater(east["metres"], west["metres"] + 300, "around the block, against it")
        on_foot = route.route(arc, place(0, 0), place(4, 0), "walk")
        self.assertAlmostEqual(on_foot["metres"], on_foot["straight"], delta=5, msg="a one way street is two way on foot")

    def test_a_lane_that_stops_short_of_the_road_still_meets_it(self):
        start = fake_tiles.lonlat(X0 + STEP * 2 + STEP // 2, Y0 + STEP * 2 - 6 - 600, Z)
        arc = self.archive(town(spur_gap=6))  # about 2.6 m short
        r = route.route(arc, start, place(0, 2), "walk")
        self.assertTrue(r["found"])
        self.assertLess(r["off_road"][0], 2, "it starts on the lane itself")
        self.assertIn("Spur Lane", r["steps"][0]["text"])
        with mock.patch.object(route.Network, "REACH", 0.5):  # without joining, the lane is an island
            alone = route.route(arc, start, place(0, 2), "walk")
        self.assertGreater(alone["off_road"][0], 100)
        far = self.archive(town(spur_gap=60))  # about 26 m: a dead end, left as one
        self.assertGreater(route.route(far, start, place(0, 2), "walk")["off_road"][0], 100)

    def test_what_is_too_far_or_off_the_map_says_why(self):
        arc = self.archive(town())
        far = route.route(arc, place(0, 0), (place(0, 0)[0] + 1.0, place(0, 0)[1]), "walk")
        self.assertFalse(far["found"])
        self.assertIn("too far", far["why"].lower())
        empty = route.route(arc, (7.55, 43.85), (7.56, 43.86), "walk")
        self.assertFalse(empty["found"])
        with self.assertRaises(ValueError):
            route.route(arc, place(0, 0), place(1, 1), "fly")


class RouteApiTests(unittest.TestCase):
    def test_the_dashboard_finds_the_way_on_the_map_that_holds_both_places(self):
        import server
        vault = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, vault, ignore_errors=True)
        os.makedirs(os.path.join(vault, "maps"))
        fake_tiles.write(os.path.join(vault, "maps", "town.pmtiles"), town(), Z, bounds=(7.3, 43.6, 7.6, 43.9))
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        self.addCleanup(httpd.server_close)
        self.addCleanup(httpd.shutdown)
        base = "http://127.0.0.1:%d/api/route" % httpd.server_address[1]
        a, b = place(0, 0), place(4, 4)

        def ask(query):
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            try:
                with opener.open(base + query, timeout=20) as r:
                    return r.status, json.loads(r.read())
            except urllib.error.HTTPError as e:
                return e.code, json.loads(e.read())
        with mock.patch.object(server, "VAULT", vault):
            status, r = ask("?from=%f,%f&to=%f,%f&mode=drive" % (a[1], a[0], b[1], b[0]))
            self.assertEqual((status, r["found"], r["mode"]), (200, True, "drive"))
            status, r = ask("?from=%f,%f&to=10,10" % (a[1], a[0]))
            self.assertEqual((status, r["found"]), (200, False))
            self.assertIn("genesis maps region", r["why"])
            self.assertEqual(ask("?from=x&to=1,2")[0], 400)
            self.assertEqual(ask("?from=1,2&to=1,2&mode=fly")[0], 400)


if __name__ == "__main__":
    unittest.main()
