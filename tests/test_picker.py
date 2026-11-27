"""Choosing downloads from the dashboard: what each shelf costs on this drive, what a tick brings down,
the download running on the box with its progress, and only the box itself starting or stopping one."""

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "dashboard"))

from genesis import catalog, fetch, picker  # noqa: E402
from genesis.util import read_json, write_json  # noqa: E402

LOCKED = [  # a catalog of its own, so the tests never change when the real one does
    ("survival", "water_book", {"kind": "zim", "dest": "zim", "tier": "seed", "version": "2", "lang": "eng",
                                "title": "Water", "files": [{"filename": "water.zim", "size": 300}]}),
    ("survival", "big_book", {"kind": "zim", "dest": "zim", "tier": "ark", "version": "1", "lang": "eng",
                              "title": "Big", "files": [{"filename": "big.zim", "size": 5000}]}),
    ("survival", "french_book", {"kind": "zim", "dest": "zim", "tier": "seed", "version": "1", "lang": "fra",
                                 "title": "Eau", "files": [{"filename": "eau.zim", "size": 70}]}),
    ("ai", "ollama_runtime", {"kind": "file", "dest": "tools", "tier": "seed", "version": "0.40",
                              "title": "Ollama", "files": [{"filename": "ollama.zip", "size": 100}]}),
    ("ai", "qwen35_4b_abliterated", {"kind": "model", "tier": "ark", "version": "1", "model": "q:4b",
                                     "title": "Qwen 4B", "size": 4000}),
    ("services", "img_x", {"kind": "image", "tier": "seed", "version": "1", "title": "Image"}),
]


def fake_locked(packs=None):
    return iter([e for e in LOCKED if not packs or e[0] in packs])


class PickerTests(unittest.TestCase):
    def setUp(self):
        self.vault = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.vault, ignore_errors=True)
        os.makedirs(os.path.join(self.vault, ".genesis"))
        for p in (mock.patch.object(catalog, "all_locked", side_effect=fake_locked),
                  mock.patch.object(catalog, "STARTER_ITEMS", ("water_book",)),
                  mock.patch("genesis.util.memory", return_value=(16e9, None)),
                  mock.patch.object(catalog, "load_pack", side_effect=lambda n: {"title": n.title(), "about": "about " + n, "items": []})):
            p.start()
            self.addCleanup(p.stop)

    def test_each_shelf_says_what_each_step_costs_on_this_drive(self):
        o = picker.options(self.vault)
        self.assertEqual([s["pack"] for s in o["shelves"]], ["survival", "ai"], "what helps first; images come with Docker")
        survival = o["shelves"][0]
        self.assertEqual(survival["tiers"]["seed"], {"bytes": 300, "to_get": 300, "items": 1, "of": 1, "unsized": 0},
                         "the French book is not for this English drive")
        self.assertEqual(survival["tiers"]["ark"]["to_get"], 5300)
        self.assertEqual((o["starter"]["items"], o["assistant"]["name"], o["assistant"]["to_get"]), (1, "Qwen 3.5 4B", 4100))
        write_json(os.path.join(self.vault, ".genesis", "state.json"), {"water_book": {"version": "2", "files": ["zim/water.zim"]}})
        again = picker.options(self.vault)
        self.assertEqual(again["shelves"][0]["tiers"]["seed"]["items"], 0, "on the drive already")
        self.assertEqual(again["shelves"][0]["tiers"]["ark"]["to_get"], 5000)
        write_json(os.path.join(self.vault, ".genesis", "state.json"), {"water_book": {"version": "1", "files": ["zim/water.zim"]}})
        self.assertEqual(picker.options(self.vault)["shelves"][0]["tiers"]["seed"]["items"], 1, "a newer version is worth getting")

    def test_a_tick_brings_down_what_is_not_here_yet(self):
        ids = lambda choices: sorted(i for _, i, _, _ in picker.chosen(self.vault, choices))  # noqa: E731
        self.assertEqual(ids([{"pack": "survival", "tier": "ark"}]), ["big_book", "water_book"])
        self.assertEqual(ids([{"set": "starter"}, {"set": "assistant"}]), ["ollama_runtime", "qwen35_4b_abliterated", "water_book"])
        self.assertEqual(picker.cost(self.vault, [{"pack": "survival", "tier": "seed"}, {"set": "starter"}]),
                         {"to_get": 300, "items": 1, "unsized": 0}, "counted once when two ticks hold it")
        with self.assertRaises(ValueError):
            picker.chosen(self.vault, [{"pack": "survival", "tier": "everything"}])

    def test_a_download_runs_in_the_background_and_says_how_far_it_has_come(self):
        started = []

        class Proc:
            pid = os.getpid()  # alive for as long as the test runs

        def popen(cmd, **kw):
            started.append((cmd, kw))
            return Proc()
        with mock.patch.object(picker.subprocess, "Popen", side_effect=popen):
            st = picker.start(self.vault, [{"pack": "survival", "tier": "ark"}], python="py")
            with self.assertRaises(ValueError):
                picker.start(self.vault, [{"set": "starter"}], python="py")
        cmd, kw = started[0]
        self.assertEqual(cmd, ["py", "-m", "genesis.picker", "run", self.vault])
        self.assertEqual(kw["cwd"], ROOT)
        folder = os.path.join(self.vault, ".genesis", "get")
        with open(os.path.join(folder, "ids.txt")) as f:
            self.assertEqual(sorted(f.read().split()), ["big_book", "water_book"])
        self.assertIn("big_book", fetch.load_config(self.vault)["also"], "kept on top of the tier for the next update")
        self.assertEqual((st["running"], st["items"], st["done"], st["bytes"], st["so_far"]), (True, 2, 0, 5300, 0))
        os.makedirs(os.path.join(self.vault, "zim"))
        with open(os.path.join(self.vault, "zim", "big.zim.part"), "wb") as f:
            f.write(b"x" * 1000)
        write_json(os.path.join(self.vault, ".genesis", "state.json"), {"water_book": {"version": "2", "files": ["zim/water.zim"]}})
        with open(os.path.join(folder, "log.txt"), "w") as f:
            f.write("[1/2] survival: Water\n  ok water.zim\n[2/2] survival: Big\n")
        st = picker.status(self.vault)
        self.assertEqual((st["done"], st["so_far"], st["now"], st["log"][-1]), (1, 1300, ["Big"], "[2/2] survival: Big"))
        write_json(os.path.join(folder, "done.json"), {"code": 0, "ended": time.time()})
        st = picker.status(self.vault)
        self.assertEqual((st["running"], st["result"]["code"], st["stopped"]), (False, 0, False))
        with self.assertRaises(ValueError):
            picker.start(self.vault, [{"pack": "survival", "tier": "seed"}], python="py")  # all on the drive now

    def test_the_download_is_the_ordinary_fetch_of_the_ids_written_down(self):
        folder = os.path.join(self.vault, ".genesis", "get")
        os.makedirs(folder)
        with open(os.path.join(folder, "ids.txt"), "w") as f:
            f.write("water_book\nbig_book\n")
        seen = []
        with mock.patch.object(fetch, "fetch", side_effect=lambda a: seen.append(fetch._selection(a, a.vault)[1]) or 0), \
                mock.patch("genesis.box.ready") as ready:
            self.assertEqual(picker.run(self.vault), 0)
        self.assertEqual(sorted(i for _, i, _, _ in seen[0]), ["big_book", "water_book"])
        self.assertEqual(ready.call_count, 1, "new books listed for the reader, new programs unpacked")
        self.assertEqual(read_json(os.path.join(folder, "done.json"))["code"], 0)

    @unittest.skipIf(os.name == "nt", "the process group test is for Linux and the Mac")
    def test_stop_ends_the_download_and_what_it_started(self):
        folder = os.path.join(self.vault, ".genesis", "get")
        os.makedirs(folder)
        proc = subprocess.Popen([sys.executable, "-c", "import subprocess, sys, time; "
                                 "subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)']); time.sleep(60)"],
                                start_new_session=True)
        self.addCleanup(lambda: proc.poll() is None and proc.kill())
        write_json(os.path.join(folder, "job.json"), {"pid": proc.pid, "items": [], "bytes": 0})
        time.sleep(0.3)
        self.assertTrue(picker.status(self.vault)["running"])
        st = picker.stop(self.vault)
        proc.wait(timeout=10)
        self.assertEqual((st["running"], st["stopped"]), (False, True))
        time.sleep(0.5)
        out = subprocess.run(["ps", "-eo", "pgid=,stat="], capture_output=True, text=True).stdout.split("\n")
        left = [ln for ln in out if ln.split() and ln.split()[0] == str(proc.pid) and not ln.split()[1].startswith("Z")]
        self.assertFalse(left, "nothing of it left running")


class PickerApiTests(unittest.TestCase):
    def setUp(self):
        import server
        self.server = server
        self.vault = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.vault, ignore_errors=True)
        os.makedirs(os.path.join(self.vault, ".genesis"))
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.addCleanup(self.httpd.server_close)
        self.addCleanup(self.httpd.shutdown)
        self.base = "http://127.0.0.1:%d" % self.httpd.server_address[1]
        for p in (mock.patch.object(server, "VAULT", self.vault), mock.patch.object(catalog, "all_locked", side_effect=fake_locked),
                  mock.patch.object(catalog, "load_pack", side_effect=lambda n: {"title": n, "items": []})):
            p.start()
            self.addCleanup(p.stop)
        server._cache.clear()

    def call(self, path, data=None):
        req = urllib.request.Request(self.base + path, data=None if data is None else json.dumps(data).encode(),
                                     headers={"Content-Type": "application/json"} if data is not None else {})
        try:
            with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(req, timeout=10) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())

    def test_only_the_box_itself_started_from_the_drive_may_start_a_download(self):
        with mock.patch.object(self.server, "BARE", False):
            status, got = self.call("/api/get")
            self.assertEqual((status, got["can"]), (200, False))
            self.assertIn("genesis fetch", got["why"])
            self.assertEqual(self.call("/api/get/start", {"choices": [{"set": "starter"}]})[0], 403)
        with mock.patch.object(self.server, "BARE", True):
            self.assertTrue(self.call("/api/get")[1]["can"])
            with mock.patch.object(self.server, "_here", return_value=False):
                status, got = self.call("/api/get/start", {"choices": [{"set": "starter"}]})
                self.assertEqual(status, 403)
                self.assertIn("at the box itself", got["error"])
            self.assertEqual(self.call("/api/get/start", {"choices": []})[0], 400)
            with mock.patch.object(picker, "start", return_value={"running": True}) as start:
                self.assertEqual(self.call("/api/get/start", {"choices": [{"pack": "survival", "tier": "seed"}]}),
                                 (200, {"running": True}))
            self.assertEqual(start.call_args[0][1], [{"pack": "survival", "tier": "seed"}])
            with mock.patch.object(picker, "start", side_effect=ValueError("a download is already running")):
                self.assertEqual(self.call("/api/get/start", {"choices": [{"set": "starter"}]})[0], 409)
        status, got = self.call("/api/get/cost?choices=" + urllib.request.quote(json.dumps([{"pack": "survival", "tier": "ark"}])))
        self.assertEqual((status, got["items"]), (200, 2))
        self.assertEqual(self.call("/api/get/cost?choices=nonsense")[0], 400)


if __name__ == "__main__":
    unittest.main()
