"""Offline tests. Run with: python3 -m unittest discover -s tests -v"""

import io
import json
import os
import re
import shutil
import sys
import tarfile
import tempfile
import threading
import unittest
import urllib.request
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from genesis import catalog, maps, poi, resolvers  # noqa: E402
from genesis.fetch import _safe_join, _untar  # noqa: E402
from genesis.util import FetchError, fkey, memory, parse_checksums, uptime  # noqa: E402

# The systems where the machine facts below must be read, not merely survived.
MEASURED = sys.platform.startswith("linux") or os.name == "nt"


class FakeNet:
    """Answers from a dict so resolvers can be tested with no network."""

    def __init__(self, pages):
        self.pages = pages

    def get(self, url, headers=None):
        if url not in self.pages:
            raise FetchError("HTTP 404 for " + url)
        data = self.pages[url]
        return data if isinstance(data, bytes) else data.encode("utf-8")

    def text(self, url, headers=None):
        return self.get(url).decode("utf-8")

    def json(self, url, headers=None):
        return json.loads(self.get(url))

    def head(self, url, headers=None):
        return url, {"content-length": "123", "last-modified": "Fri, 13 Nov 2026 04:47:28 GMT"}


KIWIX = """<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom">
<entry><title>Zig Docs</title><updated>2026-08-01T00:00:00Z</updated><language>eng</language><name>devdocs_en_zig</name><flavour/><category/>
 <link rel="http://opds-spec.org/acquisition/open-access" type="application/x-zim" href="https://lb.download.kiwix.org/zim/devdocs/devdocs_en_zig_2026-08.zim.meta4" length="1000"/></entry>
<entry><title>Wikipedia</title><updated>2026-08-25T00:00:00Z</updated><language>eng</language><name>wikipedia_en_all</name><flavour>maxi</flavour><category>wikipedia</category>
 <link rel="http://opds-spec.org/acquisition/open-access" type="application/x-zim" href="https://lb.download.kiwix.org/zim/wikipedia/wikipedia_en_all_maxi_2026-08.zim.meta4" length="5000"/></entry>
<entry><title>Wikipedia</title><updated>2026-06-01T00:00:00Z</updated><language>eng</language><name>wikipedia_en_all</name><flavour>nopic</flavour><category>wikipedia</category>
 <link rel="http://opds-spec.org/acquisition/open-access" type="application/x-zim" href="https://lb.download.kiwix.org/zim/wikipedia/wikipedia_en_all_nopic_2026-06.zim.meta4" length="2000"/></entry>
<entry><title>Wikipédia</title><updated>2026-05-01T00:00:00Z</updated><language>fra</language><name>wikipedia_fr_all</name><flavour>nopic</flavour><category>wikipedia</category>
 <link rel="http://opds-spec.org/acquisition/open-access" type="application/x-zim" href="https://lb.download.kiwix.org/zim/wikipedia/wikipedia_fr_all_nopic_2026-05.zim.meta4" length="3000"/></entry>
</feed>"""

EBIBLE = ('"languageCode","translationId","languageName","languageNameInEnglish","title","shortTitle","Redistributable",'
          '"Copyright","UpdateDate","OTbooks","NTbooks","DCbooks","textDirection","downloadable","script"\n'
          '"hbo","hboWLC","עברית","Hebrew","כתבי הקודש","Westminster Leningrad Codex","True","public domain","2017-12-27","39","0","0","rtl","True","Hebrew"\n'
          '"eng","engwebp","English","English","World English Bible","World English Bible","True","public domain","2026-10-30","39","27","0","ltr","True","Latin"\n'
          '"aai","aai","Miniafia","Miniafia","NT","Miniafia NT","True","Copyright 2009 Somebody","2026-03-09","0","27","0","ltr","True","Latin"\n'
          '"xxx","locked","X","X","X","X","False","Copyright","2026-01-01","39","27","0","ltr","True","Latin"\n')


class UtilTests(unittest.TestCase):
    def test_checksum_formats(self):
        text = "abc123" + "0" * 58 + " *ubuntu.iso\n" + "f" * 64 + "  debian.iso\nSHA256 (rocky.iso) = " + "a" * 64 + "\n# comment\n"
        self.assertEqual([f for _, f in parse_checksums(text)], ["ubuntu.iso", "debian.iso", "rocky.iso"])

    def test_version_order_ignores_architecture_digits(self):
        names = ["ubuntu-26.04-desktop-amd64.iso", "ubuntu-26.04.1-desktop-amd64.iso"]
        self.assertEqual(max(names, key=fkey), "ubuntu-26.04.1-desktop-amd64.iso")
        self.assertEqual(max(["9.2", "9.10", "10.0"], key=fkey), "10.0")


class ResolverTests(unittest.TestCase):
    def test_kiwix_single_and_rule(self):
        net = FakeNet({resolvers.KIWIX_CATALOG: KIWIX})
        res = resolvers.r_kiwix({"name": "wikipedia_en_all", "flavour": "maxi"}, net)
        f = res["files"][0]
        self.assertEqual(res["version"], "2026-08")
        self.assertEqual(f["url"], "https://download.kiwix.org/zim/wikipedia/wikipedia_en_all_maxi_2026-08.zim")
        self.assertTrue(f["sha256_url"].endswith(".zim.sha256"))
        with self.assertRaises(resolvers.ResolveError):
            resolvers.r_kiwix({"name": "wikipedia_en_all", "flavour": "mini"}, net)
        rule = {"category": "wikipedia", "regex": "^wikipedia_[a-z-]+_all$", "flavours": ["maxi", "nopic"]}
        got = {over["id"]: over["lang"] for over, _ in resolvers.x_kiwix_rule(rule, net)}
        self.assertEqual(got, {"wikipedia_en_all.maxi": "eng", "wikipedia_fr_all.nopic": "fra"})

    def test_ebible_respects_redistribution(self):
        net = FakeNet({resolvers.EBIBLE_CSV: EBIBLE})
        ids = [o["id"] for o, _ in resolvers.x_ebible({"select": "redistributable"}, net)]
        self.assertEqual(sorted(ids), ["bible_aai", "bible_engwebp", "bible_hboWLC"])
        pd = [o["id"] for o, _ in resolvers.x_ebible({"select": "public_domain"}, net)]
        self.assertNotIn("bible_aai", pd)
        over, res = resolvers.x_ebible({"select": "ids", "ids": ["hboWLC"], "always": ["hboWLC"]}, net)[0]
        self.assertTrue(over["any_lang"])
        self.assertEqual(res["meta"]["direction"], "rtl")
        with self.assertRaises(resolvers.ResolveError):  # a locked translation must never be offered
            resolvers.x_ebible({"select": "ids", "ids": ["locked"]}, net)

    def test_github_release_assets(self):
        rel = {"tag_name": "v1.31.2", "published_at": "2026-10-01T00:00:00Z", "assets": [
            {"name": "go-pmtiles_1.31.2_Linux_x86_64.tar.gz", "size": 10, "digest": "sha256:" + "a" * 64,
             "browser_download_url": "https://github.com/protomaps/go-pmtiles/releases/download/v1.31.2/go-pmtiles_1.31.2_Linux_x86_64.tar.gz"},
            {"name": "go-pmtiles_1.31.2_Linux_arm64.tar.gz", "size": 9,
             "browser_download_url": "https://example.invalid/arm64"}]}
        net = FakeNet({"https://api.github.com/repos/protomaps/go-pmtiles/releases/latest": json.dumps(rel)})
        pack = catalog.load_pack("tools")
        src = next(i for i in pack["items"] if i["id"] == "pmtiles")["source"]
        res = resolvers.r_github(src, net)
        by = {f["platform"]: f for f in res["files"]}
        self.assertEqual(res["version"], "1.31.2")
        self.assertEqual(by["linux-x64"]["sha256"], "a" * 64)
        self.assertIn("linux-arm64", by)
        self.assertTrue(res["warnings"])  # no Windows or macOS asset in this fixture

    def test_checksum_file_with_directory_index(self):
        net = FakeNet({"https://m.invalid/stable/": '<a href="22/">22/</a><a href="22.3/">22.3/</a><a href="22.1/">',
                       "https://m.invalid/stable/22.3/sha256sum.txt": "b" * 64 + " *linuxmint-22.3-cinnamon-64bit.iso\n"})
        res = resolvers.r_checksum_file({"url": "https://m.invalid/stable/{dir}/sha256sum.txt", "pattern": "cinnamon-64bit\\.iso$",
                                         "index": {"url": "https://m.invalid/stable/", "pattern": 'href="(\\d+(?:\\.\\d+)?)/"'}}, net)
        self.assertEqual(res["version"], "22.3")
        self.assertEqual(res["files"][0]["url"], "https://m.invalid/stable/22.3/linuxmint-22.3-cinnamon-64bit.iso")

    def test_image_references(self):
        self.assertEqual(resolvers.parse_image("nginx:alpine"), ("docker.io", "library/nginx", "alpine"))
        self.assertEqual(resolvers.parse_image("ollama/ollama"), ("docker.io", "ollama/ollama", "latest"))
        self.assertEqual(resolvers.parse_image("ghcr.io/kiwix/kiwix-serve:3.8.1"), ("ghcr.io", "kiwix/kiwix-serve", "3.8.1"))


class CatalogTests(unittest.TestCase):
    def test_packs_are_clean(self):
        self.assertEqual(catalog.lint(), [])

    def test_every_compose_image_is_tracked(self):
        with open(os.path.join(ROOT, "compose.yaml"), encoding="utf-8") as f:
            used = set(re.findall(r"(?m)^\s+image:\s*(\S+)", f.read()))
        tracked = {i["source"]["image"] for i in catalog.load_pack("services")["items"]}
        self.assertEqual(used - tracked, set(), "add these images to catalog/packs/services.json")
        self.assertEqual(tracked - used, set(), "these tracked images are not used by compose.yaml")

    def test_dashboard_services_exist_in_compose(self):
        with open(os.path.join(ROOT, "compose.yaml"), encoding="utf-8") as f:
            text = f.read()
        with open(os.path.join(ROOT, "dashboard", "services.json"), encoding="utf-8") as f:
            for s in json.load(f):
                self.assertRegex(text, r"(?m)^  %s:\s*$" % re.escape(s["host"]), s["id"])
                self.assertIn("%d:%d" % (s["public"], s["port"]), text, s["id"])


class PoiTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.db_path = os.path.join(self.tmp, "poi", "t.sqlite")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_classify(self):
        cats = poi.categories()
        self.assertEqual(poi.classify({"amenity": "drinking_water"}, cats), ("water", "drinking_water"))
        self.assertEqual(poi.classify({"amenity": "hospital", "shop": "supermarket"}, cats), ("medical", "hospital"))
        self.assertIsNone(poi.classify({"power": "generator", "generator:source": "solar"}, cats))
        self.assertIsNone(poi.classify({"amenity": "cafe"}, cats))

    def test_ingest_and_nearest(self):
        feats = [
            {"type": "Feature", "geometry": {"type": "Point", "coordinates": [7.4246, 43.7384]},
             "properties": {"@type": "node", "@id": 1, "amenity": "drinking_water", "name": "Near"}},
            {"type": "Feature", "geometry": {"type": "Point", "coordinates": [7.5, 43.8]},
             "properties": {"@type": "node", "@id": 2, "natural": "spring", "name": "Far"}},
            {"type": "Feature", "geometry": {"type": "Polygon", "coordinates": [[[7.41, 43.73], [7.411, 43.73], [7.411, 43.731], [7.41, 43.731], [7.41, 43.73]]]},
             "properties": {"@type": "way", "@id": 100, "amenity": "hospital", "name": "Hospital"}},
            {"type": "Feature", "geometry": {"type": "Point", "coordinates": [7.4, 43.7]}, "properties": {"@type": "node", "@id": 3, "amenity": "cafe"}}]
        db = poi.connect(self.db_path, create=True)
        lines = ["\x1e" + json.dumps(f) for f in feats]
        self.assertEqual(poi.ingest_geojsonseq(db, lines), 3)
        self.assertEqual(poi.ingest_geojsonseq(db, lines), 0, "loading the same region twice must not duplicate")
        near = poi.nearest(db, 43.7384, 7.4246, "water", 2)
        self.assertEqual([p["name"] for p in near], ["Near", "Far"])
        self.assertEqual(near[0]["km"], 0.0)
        self.assertEqual(near[1]["compass"], "NE")
        hospital = poi.nearest(db, 43.7384, 7.4246, "medical", 1)[0]
        self.assertAlmostEqual(hospital["lat"], 43.7305, places=4)
        self.assertEqual(poi.stats(db)["total"], 3)
        self.assertEqual(len(poi.in_bbox(db, 7.40, 43.72, 7.45, 43.76)), 2)
        out = io.StringIO()
        poi.export(db, "geojson", "water", out)
        self.assertEqual(len(json.loads(out.getvalue())["features"]), 2)

    def test_merge_databases(self):
        def make(path, osm_id, name):
            db = poi.connect(path, create=True)
            feat = {"type": "Feature", "geometry": {"type": "Point", "coordinates": [7.42, 43.73]},
                    "properties": {"@type": "node", "@id": osm_id, "amenity": "fuel", "name": name}}
            poi.ingest_geojsonseq(db, [json.dumps(feat)])
            db.close()
        a, b = os.path.join(self.tmp, "a.sqlite"), os.path.join(self.tmp, "b.sqlite")
        make(a, 1, "A")
        make(b, 2, "B")
        self.assertEqual(poi.merge(self.db_path, [a, b, a]), 2, "merging the same region twice adds nothing")
        db = poi.connect(self.db_path)
        self.assertEqual(len(poi.nearest(db, 43.73, 7.42, "fuel", 5)), 2, "merged places are in the spatial index")

    @unittest.skipUnless(shutil.which("osmium"), "osmium-tool not installed")
    def test_build_from_osm_with_osmium(self):
        added = poi.build(os.path.join(ROOT, "tests", "fixtures", "sample.osm"), self.db_path, replace=True)
        self.assertEqual(added, 8)
        s = poi.stats(poi.connect(self.db_path))
        self.assertEqual(s["categories"]["water"]["total"], 2)
        self.assertNotIn("generator", s["categories"]["power"]["kinds"])


class MapAndArchiveTests(unittest.TestCase):
    def test_poly_to_geojson(self):
        poly = "monaco\n1\n   7.40   43.72\n   7.45   43.72\n   7.45   43.76\nEND\n!2\n   7.41   43.73\n   7.42   43.73\n   7.42   43.74\nEND\nEND\n"
        g = maps.poly_to_geojson(poly)
        self.assertEqual(g["type"], "MultiPolygon")
        self.assertEqual(len(g["coordinates"]), 1)
        self.assertEqual(len(g["coordinates"][0]), 2, "the hole belongs to the first polygon")
        self.assertEqual(g["coordinates"][0][0][0], g["coordinates"][0][0][-1], "rings are closed")

    def test_archives_cannot_escape(self):
        base = tempfile.mkdtemp()
        try:
            with self.assertRaises(FetchError):
                _safe_join(base, "../../etc/passwd")
            src = os.path.join(base, "a.tar.gz")
            with tarfile.open(src, "w:gz") as t:
                data = b"hello"
                info = tarfile.TarInfo("top/fonts/a.txt")
                info.size = len(data)
                t.addfile(info, io.BytesIO(data))
            out = os.path.join(base, "out")
            _untar(src, out)
            self.assertTrue(os.path.exists(os.path.join(out, "fonts", "a.txt")), "top folder is stripped")
        finally:
            shutil.rmtree(base, ignore_errors=True)


class DashboardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.vault = tempfile.mkdtemp()
        os.makedirs(os.path.join(cls.vault, "maps"))
        with open(os.path.join(cls.vault, "maps", "t.pmtiles"), "wb") as f:
            f.write(bytes(range(256)) * 4)
        os.makedirs(os.path.join(cls.vault, ".genesis"))
        with open(os.path.join(cls.vault, ".genesis", "state.json"), "w") as f:
            f.write('{"secret": {}}')
        os.environ["GENESIS_VAULT"] = cls.vault
        sys.path.insert(0, os.path.join(ROOT, "dashboard"))
        import server
        cls.httpd = server.ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        cls.base = "http://127.0.0.1:%d" % cls.httpd.server_address[1]
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        shutil.rmtree(cls.vault, ignore_errors=True)

    def get(self, path, headers=None):
        req = urllib.request.Request(self.base + path, headers=headers or {})
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        try:
            with opener.open(req, timeout=10) as r:
                return r.status, r.read(), dict(r.headers)
        except urllib.error.HTTPError as e:
            return e.code, e.read(), dict(e.headers)

    def test_pages_and_api(self):
        self.assertEqual(self.get("/")[0], 200)
        status, body, _ = self.get("/api/system")
        self.assertEqual(status, 200)
        system = json.loads(body)
        self.assertIn("disk_free", system)
        if MEASURED:
            self.assertGreater(system["ram_total"], 0)
            self.assertGreaterEqual(system["uptime"], 0)
        self.assertEqual(json.loads(self.get("/api/maps")[1])["maps"][0]["name"], "t")
        self.assertIn("water", json.loads(self.get("/api/poi/categories")[1]))

    def test_system_without_a_load_average(self):
        # Windows has no os.getloadavg at all, which is not the OSError other systems raise.
        import server
        with mock.patch.object(server.os, "getloadavg", side_effect=AttributeError("getloadavg"), create=True):
            info = server.system()
        self.assertNotIn("load", info)
        self.assertIn("disk_free", info)

    def test_range_requests(self):
        status, body, headers = self.get("/maps/t.pmtiles", {"Range": "bytes=10-19"})
        self.assertEqual(status, 206)
        self.assertEqual(body, bytes(range(10, 20)))
        self.assertEqual(headers["Content-Range"], "bytes 10-19/1024")

    def test_private_files_stay_private(self):
        for path in ("/maps/../.genesis/state.json", "/maps/%2e%2e/.genesis/state.json", "/.genesis/state.json"):
            self.assertEqual(self.get(path)[0], 404, path)


class MachineTests(unittest.TestCase):
    def test_memory_and_uptime(self):
        total, free = memory()
        if MEASURED:
            self.assertGreater(total, 64 * 1024 * 1024)
            self.assertTrue(0 < free <= total, (total, free))
            self.assertGreaterEqual(uptime(), 0)
        else:  # elsewhere an answer may be missing, never wrong
            self.assertTrue(total is None or total > 0, total)
            self.assertTrue(free is None or 0 < free <= total, (total, free))

    def test_scripts_keep_unix_line_endings(self):
        # A copy cloned on Windows has to run on a Raspberry Pi. .gitattributes is what keeps that true.
        for name in ("install.sh", "bin/genesis", "tools/monaco_vault.sh", "compose.yaml"):
            with open(os.path.join(ROOT, name), "rb") as f:
                self.assertNotIn(b"\r", f.read(), name)


if __name__ == "__main__":
    unittest.main()
