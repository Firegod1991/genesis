"""How the work is shared out: no source takes every slot, and a tier can carry extra items."""

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

from genesis import fetch  # noqa: E402
from genesis.util import read_json  # noqa: E402


class SpreadTests(unittest.TestCase):
    def setUp(self):
        self.vault = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.vault, ignore_errors=True)

    def test_a_slow_source_never_takes_every_slot_while_others_wait(self):
        slow = [("library", "book%d" % n, {"kind": "zim", "dest": "zim", "version": "1", "title": "Book %d" % n},
                 [{"url": fetch.KIWIX + "zim/books/book%d.zim" % n, "filename": "book%d.zim" % n, "size": 5}])
                for n in range(12)]
        quick = [("tools", "tool%d" % n, {"kind": "tool", "dest": "tools", "version": "1", "title": "Tool %d" % n},
                  [{"url": "https://quick.example/tool%d.bin" % n, "filename": "tool%d.bin" % n, "size": 5}])
                 for n in range(6)]
        lock, flying, seen, started = threading.Lock(), [], [], [0]

        def download(url, dest, size=None):
            kind = "slow" if "/zim/books/" in url else "quick"
            with lock:
                flying.append(kind)
                started[0] += kind == "quick"
                seen.append((flying.count("slow"), started[0]))
            time.sleep(0.05 if kind == "slow" else 0.01)
            with lock:
                flying.remove(kind)
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            with open(dest, "w") as f:
                f.write("hello")

        args = argparse.Namespace(vault=self.vault, yes=True, keep_going=True, jobs=4)
        with mock.patch.object(fetch, "_selection", return_value=({}, slow + quick)), \
                mock.patch.object(fetch.catalog, "all_locked", return_value=[]), \
                mock.patch.object(fetch, "_download", side_effect=download):
            self.assertEqual(fetch.fetch(args), 0)
        self.assertEqual(len(read_json(os.path.join(self.vault, ".genesis", "state.json"))), 18)
        self.assertLessEqual(max(slow for slow, quick in seen if quick < 6), 2,
                             "half the slots at most while the quick source still has items waiting")
        self.assertGreater(max(slow for slow, quick in seen), 2, "and every slot once nothing else is left")

    def test_what_an_item_is_downloaded_from(self):
        book = [{"url": fetch.KIWIX + "zim/books/gutenberg_en_lcc-a_2026-11a.zim"}]
        self.assertEqual(fetch._source({"kind": "zim"}, book), fetch.KIWIX + "zim/books")
        self.assertEqual(fetch._source({"kind": "zim"}, [{"url": fetch.KIWIX + "zim/wikipedia/w.zim"}]), fetch.KIWIX + "zim/wikipedia")
        self.assertEqual(fetch._source({"kind": "tool"}, [{"url": "https://github.com/o/r/releases/download/v1/x.zip"}]), "github.com")
        self.assertEqual(fetch._source({"kind": "model", "model": "tiny:1"}, []), "models")

    def test_also_adds_named_items_to_the_tier_and_is_remembered(self):
        def entry(tier):
            return {"tier": tier, "lang": "eng", "kind": "tool", "version": "1", "files": [{"filename": "f", "url": "https://x.example/f"}]}
        locked = [("tools", "small", entry("seed")), ("maps", "planet", entry("eden")), ("isos", "huge", entry("eden"))]

        def ids(args):
            with mock.patch.object(fetch.catalog, "all_locked", side_effect=lambda packs=None: iter(locked)):
                return sorted(i for _, i, _, _ in fetch._selection(args, self.vault, save=True)[1])

        self.assertEqual(ids(argparse.Namespace(vault=self.vault, tier="seed")), ["small"])
        self.assertEqual(ids(argparse.Namespace(vault=self.vault, tier="seed", also=["planet"])), ["planet", "small"])
        self.assertEqual(read_json(os.path.join(self.vault, ".genesis", "config.json"))["also"], ["planet"])
        self.assertEqual(ids(argparse.Namespace(vault=self.vault)), ["planet", "small"], "the next update keeps it")
        self.assertEqual(ids(argparse.Namespace(vault=self.vault, id=["small"])), ["small"], "asking by id means only that")


if __name__ == "__main__":
    unittest.main()
