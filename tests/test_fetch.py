"""How a fetch behaves: several downloads at once, and models that land inside the vault."""

import argparse
import os
import shutil
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from genesis import fetch, seal  # noqa: E402
from genesis.util import FetchError, file_hash, read_json, write_json  # noqa: E402

STUB = [sys.executable, os.path.join(ROOT, "tests", "stub_ollama.py")]


class ModelTests(unittest.TestCase):
    def setUp(self):
        self.vault = tempfile.mkdtemp()
        self.store = os.path.join(self.vault, "models", "ollama", "models")
        self.models = fetch.Models(self.vault)
        self.addCleanup(shutil.rmtree, self.vault, ignore_errors=True)
        self.addCleanup(self.models.close)

    def world(self, command=None, stack=False, explicit=None):
        """An Ollama on this machine or not, the stack running or not, a server named by the owner or not."""
        env = {k: v for k, v in os.environ.items() if k != "GENESIS_OLLAMA"}
        if explicit:
            env["GENESIS_OLLAMA"] = explicit
        for patch in (mock.patch.object(fetch, "_ollama_command", return_value=command),
                      mock.patch.object(fetch, "_stack_ollama_running", return_value=stack),
                      mock.patch.dict(os.environ, env, clear=True)):
            patch.start()
            self.addCleanup(patch.stop)

    def test_models_land_inside_the_vault(self):
        self.world(command=STUB)
        entry = {"kind": "model", "model": "tiny:1", "version": "abc"}
        got = fetch._fetch_item(self.vault, "tiny", entry, [], None, {}, self.models)
        self.assertEqual(got, {"version": "abc", "files": [], "model": "tiny:1"})
        self.assertTrue(os.path.isfile(os.path.join(self.store, "tiny_1")), "pulled into the vault's own store")
        self.assertTrue(self.models.pull("tiny:2"), "one server serves the whole fetch")
        host = self.models.host
        self.models.close()
        with self.assertRaises(OSError, msg="the server stops when the fetch ends"):
            fetch._DIRECT.open(host + "/api/version", timeout=2)

    def test_a_model_the_registry_does_not_have_fails_the_item(self):
        self.world(command=STUB)
        with self.assertRaises(FetchError):
            self.models.pull("missing:1")

    def test_without_any_ollama_models_are_skipped(self):
        self.world()
        self.assertFalse(self.models.pull("tiny:1"))
        self.assertFalse(os.path.exists(self.store))

    def test_the_stack_does_the_pulling_while_it_runs(self):
        self.world(command=STUB, stack=True)
        self.assertEqual(self.models.start(), fetch.STACK_OLLAMA)
        self.assertIsNone(self.models.proc, "no second server beside the stack's own")

    def test_a_server_named_by_the_owner_wins(self):
        self.world(command=STUB, stack=True, explicit="10.0.0.5:11434/")
        self.assertEqual(self.models.start(), "http://10.0.0.5:11434")
        self.assertIsNone(self.models.proc)


class ParallelFetchTests(unittest.TestCase):
    def run_fetch(self, jobs, keep_going=True, broken=("item13",)):
        vault = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, vault, ignore_errors=True)
        sel = [("code", "item%d" % n, {"kind": "document", "dest": "docs", "version": "1", "title": "Item %d" % n},
                [{"url": "https://example.invalid/item%d.txt" % n, "filename": "item%d.txt" % n, "size": 5}])
               for n in range(40)]
        busy, most, before = [], [0], threading.active_count()

        def download(url, dest, size=None):
            busy.append(url)
            most[0] = max(most[0], len(busy))
            time.sleep(0.01)
            busy.remove(url)
            if os.path.basename(url)[:-4] in broken:
                raise FetchError("HTTP 404 for " + url)
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            with open(dest, "w") as f:
                f.write("hello")

        args = argparse.Namespace(vault=vault, yes=True, keep_going=keep_going, jobs=jobs)
        with mock.patch.object(fetch, "_selection", return_value=({}, sel)), \
                mock.patch.object(fetch, "_download", side_effect=download):
            code = fetch.fetch(args)
        self.assertEqual(threading.active_count(), before, "no worker is left behind")
        return code, vault, most[0], read_json(os.path.join(vault, ".genesis", "state.json"), {})

    def test_several_at_once_and_every_item_is_recorded(self):
        code, vault, most, state = self.run_fetch(jobs=8)
        self.assertEqual(code, 1, "one item failed")
        self.assertEqual(len(state), 39)
        self.assertNotIn("item13", state)
        self.assertEqual(state["item7"]["files"], ["docs/item7.txt"], "forward slashes on every system")
        self.assertTrue(os.path.isfile(os.path.join(vault, "docs", "item39.txt")))
        self.assertGreater(most, 1, "downloads overlap")
        self.assertLessEqual(most, 8, "and never more than asked for")

    def test_a_drive_filled_on_windows_verifies_anywhere(self):
        _, vault, _, state = self.run_fetch(jobs=4, broken=())
        for st in state.values():  # the way Genesis on Windows used to write it
            st["files"] = [f.replace("/", "\\") for f in st["files"]]
        write_json(os.path.join(vault, ".genesis", "state.json"), state)
        self.assertEqual(fetch._state(vault)["item7"]["files"], ["docs/item7.txt"])
        self.assertTrue(os.path.isfile(fetch._inside(vault, "docs\\item7.txt")))
        with mock.patch.object(fetch.catalog, "all_locked", return_value=[]):
            self.assertEqual(fetch.verify(argparse.Namespace(vault=vault)), 0)
        os.remove(os.path.join(vault, "docs", "item7.txt"))
        with mock.patch.object(fetch.catalog, "all_locked", return_value=[]):
            self.assertEqual(fetch.verify(argparse.Namespace(vault=vault)), 1, "a missing file is still caught")

    def test_one_at_a_time_still_works(self):
        code, _, most, state = self.run_fetch(jobs=1, broken=())
        self.assertEqual((code, most, len(state)), (0, 1, 40))

    def test_a_dead_connection_stops_the_run_early(self):
        code, _, _, state = self.run_fetch(jobs=2, keep_going=False, broken=tuple("item%d" % n for n in range(40)))
        self.assertEqual((code, state), (1, {}))


class MirrorTests(unittest.TestCase):
    URL = fetch.KIWIX + "zim/wikipedia/wikipedia_en_medicine_maxi_2026-10.zim"
    META = ('<metalink><file name="x"><url location="us" priority="1">https://slow.example/kiwix/zim/wikipedia/'
            'wikipedia_en_medicine_maxi_2026-10.zim</url>\n<url location="us" priority="2">https://quick.example/pub/'
            'zim/wikipedia/wikipedia_en_medicine_maxi_2026-10.zim</url>\n<url location="de" priority="3">https://dead.'
            'example/zim/wikipedia/wikipedia_en_medicine_maxi_2026-10.zim</url></file></metalink>')
    SPEED = {"slow.example": 6e5, "quick.example": 23e6, "dead.example": 0}

    def setUp(self):
        self.asked, self.timed = [], []
        fetch._mirror.clear()
        self.addCleanup(fetch._mirror.clear)

        class Page:
            def __enter__(page):
                return page

            def __exit__(page, *exc):
                return False

            def read(page):
                return self.META.encode()

        def http_open(url, **kw):
            self.asked.append(url)
            return Page()

        def speed(url, seconds=3.0):
            self.timed.append(url)
            return self.SPEED[url.split("/")[2]]

        for patch in (mock.patch.object(fetch, "http_open", side_effect=http_open),
                      mock.patch.object(fetch, "_speed", side_effect=speed)):
            patch.start()
            self.addCleanup(patch.stop)

    def test_the_quickest_mirror_is_found_once_and_kept_for_the_folder(self):
        quick = "https://quick.example/pub/zim/wikipedia/"
        self.assertEqual(fetch._kiwix_mirror(self.URL, 2e9), quick + "wikipedia_en_medicine_maxi_2026-10.zim")
        self.assertEqual(self.asked, [self.URL + ".meta4"])
        self.assertEqual(len(self.timed), 3, "every mirror is timed")
        other = fetch.KIWIX + "zim/wikipedia/wikipedia_en_all_maxi_2026-08.zim"
        self.assertEqual(fetch._kiwix_mirror(other, 1e11), quick + "wikipedia_en_all_maxi_2026-08.zim")
        self.assertEqual(len(self.asked), 1, "the next file in the folder costs no second comparison")

    def test_small_files_and_other_publishers_are_left_alone(self):
        self.assertIsNone(fetch._kiwix_mirror(self.URL, 5e6))
        self.assertIsNone(fetch._kiwix_mirror(self.URL, None))
        self.assertIsNone(fetch._kiwix_mirror("https://example.org/big.iso", 4e9))
        self.assertEqual(self.asked, [])

    def test_no_mirror_answering_means_the_publishers_own_address(self):
        self.SPEED = dict.fromkeys(self.SPEED, 0)
        self.assertIsNone(fetch._kiwix_mirror(self.URL, 2e9))

    def test_a_mirror_that_gives_up_hands_back_to_the_publisher(self):
        vault = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, vault, ignore_errors=True)
        dest, tried = os.path.join(vault, "zim", "m.zim"), []

        def transfer(url, part):
            tried.append(url.split("/")[2])
            if "quick.example" in url:
                raise FetchError("curl exit 22 for " + url)
            with open(part, "w") as f:
                f.write("zim")

        with mock.patch.object(fetch, "_transfer", side_effect=transfer):
            fetch._download(self.URL, dest, 2e9)
        self.assertEqual(tried, ["quick.example", "download.kiwix.org"])
        self.assertTrue(os.path.isfile(dest))


class SealTests(unittest.TestCase):
    def test_a_sealed_drive_proves_itself(self):
        vault = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, vault, ignore_errors=True)
        args = argparse.Namespace(vault=vault)
        self.assertEqual(seal.check(args), 1, "nothing to prove before the first seal")
        files = {}
        for rel, text in (("documents/a.txt", "alpha"), ("maps/world/b.bin", "beta"), ("c.txt", "gamma")):
            path = os.path.join(vault, *rel.split("/"))
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w") as f:
                f.write(text)
            files[rel] = {"bytes": len(text), "sha256": file_hash(path)}
        write_json(os.path.join(vault, "restore", "MANIFEST.json"), {"sealed": "2026-11-16", "files": files})
        self.assertEqual(seal.check(args), 0)
        with open(os.path.join(vault, "documents", "a.txt"), "w") as f:
            f.write("alphA")  # same size, one flipped letter
        self.assertEqual(seal.check(args), 1, "a changed file is caught by its hash")
        os.remove(os.path.join(vault, "c.txt"))
        self.assertEqual(seal.check(args), 1, "and so is a missing one")


if __name__ == "__main__":
    unittest.main()
