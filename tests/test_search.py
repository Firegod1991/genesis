"""Offline tests for searching the survival layers: the query plan, busy boxes, the date line, kept totals."""

import json
import os
import random
import shutil
import sqlite3
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from genesis import poi  # noqa: E402


class SearchTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.path = os.path.join(self.tmp, "poi", "t.sqlite")
        self.db = poi.connect(self.path, create=True)
        self.count = 0

    def tearDown(self):
        self.db.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def add(self, cat, lat, lon, name=None):
        self.count += 1
        poi._insert(self.db, True, cat, "kind", name or "p%d" % self.count, lat, lon, "n%d" % self.count, None)

    def exact(self, lat, lon, cat, n):
        rows = self.db.execute("SELECT name, lat, lon FROM poi WHERE cat = ?", (cat,)).fetchall()
        return [name for _, name in sorted((poi._distance(lat, lon, a, b)[0], name) for name, a, b in rows)[:n]]

    def test_spatial_index_leads_the_plan(self):
        # On the world database the category index reads a million rows where the spatial index reads ten.
        for i in range(200):
            self.add("water" if i % 2 else "fuel", 40 + i / 1000.0, 10 + i / 1000.0)
        self.db.commit()
        seen = []
        self.db.set_trace_callback(seen.append)
        poi.nearest(self.db, 40.1, 10.1, "water", 3)
        poi.in_bbox(self.db, 10, 40, 11, 41, cat="water")
        self.db.set_trace_callback(None)
        searches = [sql for sql in seen if "poi_rt" in sql and sql.lstrip().upper().startswith("SELECT P.ID")]
        self.assertGreaterEqual(len(searches), 2)
        for sql in searches:
            plan = [row[3] for row in self.db.execute("EXPLAIN QUERY PLAN " + sql)]
            self.assertIn("VIRTUAL TABLE", plan[0], plan)
            self.assertFalse([step for step in plan if "poi_cat" in step], plan)

    def test_busy_box_still_returns_the_closest(self):
        # Far more places in the first box than the search keeps in memory.
        rnd = random.Random(11)
        for _ in range(3000):
            self.add("supply", 48.85 + rnd.uniform(-0.012, 0.012), 2.35 + rnd.uniform(-0.012, 0.012))
        for _ in range(40):
            self.add("water", 48.85 + rnd.uniform(-0.4, 0.4), 2.35 + rnd.uniform(-0.4, 0.4))
        self.db.commit()
        for lat, lon in ((48.85, 2.35), (48.8561, 2.3447), (48.95, 2.2)):
            for cat, n in (("supply", 5), ("water", 7)):
                got = [p["name"] for p in poi.nearest(self.db, lat, lon, cat, n)]
                self.assertEqual(got, self.exact(lat, lon, cat, n), (lat, lon, cat))
        mixed = poi.nearest(self.db, 48.85, 2.35, None, 4)
        self.assertEqual([p["km"] for p in mixed], sorted(p["km"] for p in mixed))

    def test_search_crosses_the_date_line(self):
        self.add("water", -16.80, 179.99, "west side")
        self.add("water", -16.80, -179.99, "east side")
        self.add("water", -16.80, 179.00, "far west")
        self.db.commit()
        got = poi.nearest(self.db, -16.80, 179.998, "water", 3)
        self.assertEqual([p["name"] for p in got], ["west side", "east side", "far west"])
        self.assertLess(got[1]["km"], 2.0)
        self.assertEqual(got[1]["compass"], "E")
        boxed = poi.in_bbox(self.db, 179.9, -17, 180.1, -16.5)
        self.assertEqual(sorted(p["name"] for p in boxed), ["east side", "west side"])

    def test_search_stops_at_its_limit(self):
        self.add("fuel", 10.0, 10.0, "only one")
        self.db.commit()
        self.assertEqual(poi.nearest(self.db, 10.0, 20.0, "fuel", 3, max_km=500), [])
        self.assertEqual([p["name"] for p in poi.nearest(self.db, 10.0, 20.0, "fuel", 3)], ["only one"])
        self.assertEqual(poi.nearest(self.db, 10.0, 40.0, "fuel", 3), [], "nothing is offered from over 2000 km away")
        self.assertEqual(poi.nearest(self.db, 10.0, 10.0, "water", 3, max_km=50), [])

    def test_box_respects_category_and_limit(self):
        for i in range(30):
            self.add("water" if i % 3 else "fuel", 5 + i / 100.0, 5 + i / 100.0)
        self.db.commit()
        self.assertEqual(len(poi.in_bbox(self.db, 4, 4, 6, 6)), 30)
        self.assertEqual(len(poi.in_bbox(self.db, 4, 4, 6, 6, limit=7)), 7)
        self.assertEqual({p["cat"] for p in poi.in_bbox(self.db, 4, 4, 6, 6, cat="fuel")}, {"fuel"})
        self.assertEqual(len(poi.in_bbox(self.db, 4, 4, 6, 6, cat="fuel,water")), 30)

    def test_totals_are_kept_and_follow_new_places(self):
        self.add("water", 1.0, 1.0)
        self.add("fuel", 2.0, 2.0)
        self.db.commit()
        first = poi.stats(self.db)
        self.assertEqual((first["total"], first["categories"]["water"]["total"]), (2, 1))
        kept = json.loads(self.db.execute("SELECT value FROM meta WHERE key = 'stats'").fetchone()[0])
        self.assertEqual(kept["total"], 2)
        # A kept answer is used as it stands until a place is added.
        self.db.execute("UPDATE meta SET value = replace(value, '\"total\": 2', '\"total\": 99') WHERE key = 'stats'")
        self.assertEqual(poi.stats(self.db)["total"], 99)
        self.add("water", 3.0, 3.0)
        self.db.commit()
        self.assertEqual(poi.stats(self.db)["total"], 3)
        self.assertNotIn("stats", poi.stats(self.db)["built"])

    def test_totals_on_read_only_media(self):
        self.add("water", 1.0, 1.0)
        self.db.commit()
        locked = sqlite3.connect("file:%s?mode=ro" % self.path, uri=True)
        try:
            self.assertEqual(poi.stats(locked)["total"], 1)
        finally:
            locked.close()


if __name__ == "__main__":
    unittest.main()
