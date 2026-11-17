"""What the catalog has moved on from: volumes folded into one, and downloads nothing waits for."""

import argparse
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from genesis import fetch, refresh  # noqa: E402
from genesis.util import FetchError, read_json, write_json  # noqa: E402


class MergedVolumeTests(unittest.TestCase):
    """A publisher folds several volumes into one and goes on listing the old ones."""

    @staticmethod
    def lock(books, rule="gutenberg_every_language"):
        return {iid: {"rule": rule, "date": date, "version": date[:7], "files": [{"filename": iid + ".zim", "size": int(gb * 1e9)}]}
                for iid, date, gb in books}

    def test_the_parts_go_and_the_combined_volume_says_what_it_replaces(self):
        new = self.lock([("gutenberg_en_lcc-a", "2026-11-13", 9.67), ("gutenberg_en_lcc-c", "2025-12-05", 1.32),
                         ("gutenberg_en_lcc-d", "2025-12-06", 40.25), ("gutenberg_en_lcc-e", "2026-03-05", 10.2),
                         ("gutenberg_en_lcc-f", "2026-03-05", 9.89), ("gutenberg_en_lcc-cdef", "2026-11-13", 46.94),
                         ("gutenberg_en_lcc-p", "2026-11-13", 0.04), ("gutenberg_en_lcc-s", "2026-11-13", 3.42),
                         ("gutenberg_en_lcc-ps", "2026-11-13", 11.59), ("gutenberg_fr_all", "2026-10-02", 12.0)])
        refresh._fold(new, {})
        self.assertEqual(sorted(new), ["gutenberg_en_lcc-a", "gutenberg_en_lcc-cdef", "gutenberg_en_lcc-p",
                                       "gutenberg_en_lcc-ps", "gutenberg_en_lcc-s", "gutenberg_fr_all"])
        self.assertEqual(new["gutenberg_en_lcc-cdef"]["replaces"], ["gutenberg_en_lcc-" + x for x in "cdef"])
        self.assertNotIn("replaces", new["gutenberg_en_lcc-ps"], "PS is a shelf of its own, not P plus S")

    def test_only_a_real_merge_counts(self):
        parts = [("x_lcc-a", "2026-11-10", 5), ("x_lcc-b", "2026-11-10", 5), ("x_lcc-c", "2026-11-10", 5)]
        self.assertEqual(refresh.merged(self.lock(parts + [("x_lcc-abc", "2026-11-13", 14)])), {}, "three days apart")
        self.assertEqual(refresh.merged(self.lock(parts + [("x_lcc-abc", "2027-03-01", 2)])), {}, "far too small to hold them")
        self.assertEqual(refresh.merged(self.lock(parts[:2] + [("x_lcc-abc", "2027-03-01", 14)])), {}, "one part never existed")
        other = dict(self.lock(parts), **self.lock([("x_lcc-abc", "2027-03-01", 14)], rule="another_rule"))
        self.assertEqual(refresh.merged(other), {}, "volumes of another rule are not its parts")
        self.assertEqual(refresh.merged(self.lock(parts + [("x_lcc-abc", "2027-03-01", 14)])),
                         {"x_lcc-abc": ["x_lcc-a", "x_lcc-b", "x_lcc-c"]})

    def test_the_note_outlives_the_publisher_dropping_the_parts(self):
        old = self.lock([("shelf-cdef", "2026-11-13", 47)])
        old["shelf-cdef"]["replaces"] = ["shelf-c", "shelf-d", "shelf-e", "shelf-f"]
        new = self.lock([("shelf-cdef", "2026-12-14", 48)])
        refresh._fold(new, old)
        self.assertEqual(new["shelf-cdef"]["replaces"], ["shelf-c", "shelf-d", "shelf-e", "shelf-f"])
        again = self.lock([("shelf-cdef", "2027-01-15", 48), ("shelf-d", "2027-01-15", 40)])
        refresh._fold(again, new)
        self.assertEqual(again["shelf-cdef"]["replaces"], ["shelf-c", "shelf-e", "shelf-f"], "a part that comes back is kept")
        self.assertIn("shelf-d", again)


class TidyTests(unittest.TestCase):
    """What the catalog has moved on from does not pile up on the drive."""

    def setUp(self):
        self.vault = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.vault, ignore_errors=True)

    def put(self, rel, text="x"):
        path = os.path.join(self.vault, *rel.split("/"))
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            f.write(text)
        return path

    def test_a_combined_volume_retires_the_parts_it_replaces(self):
        part = self.put("zim/shelf-c_2025-12.zim")
        write_json(os.path.join(self.vault, ".genesis", "state.json"),
                   {"shelf-c": {"version": "2025-12", "files": ["zim/shelf-c_2025-12.zim"]}})
        sel = [("library", "shelf-cdef", {"kind": "zim", "dest": "zim", "version": "2026-11", "title": "Shelves C to F",
                                          "replaces": ["shelf-c", "shelf-d"]},
                [{"url": "https://example.invalid/shelf-cdef_2026-11.zim", "filename": "shelf-cdef_2026-11.zim", "size": 4}])]
        args = argparse.Namespace(vault=self.vault, yes=True, keep_going=True, jobs=2)
        with mock.patch.object(fetch, "_selection", return_value=({}, sel)), \
                mock.patch.object(fetch.catalog, "all_locked", return_value=[]), \
                mock.patch.object(fetch, "_download", side_effect=FetchError("no route")):
            self.assertEqual(fetch.fetch(args), 1)
        self.assertTrue(os.path.isfile(part), "the old volume stays until the new one is on the drive")
        with mock.patch.object(fetch, "_selection", return_value=({}, sel)), \
                mock.patch.object(fetch.catalog, "all_locked", return_value=[]), \
                mock.patch.object(fetch, "_download", side_effect=lambda url, dest, size=None: self.put("zim/shelf-cdef_2026-11.zim", "cdef")):
            self.assertEqual(fetch.fetch(args), 0)
        state = read_json(os.path.join(self.vault, ".genesis", "state.json"))
        self.assertEqual(sorted(state), ["shelf-cdef"])
        self.assertFalse(os.path.exists(part))

    def test_a_part_left_behind_by_an_interrupted_run_is_retired_on_the_next(self):
        self.put("zim/shelf-cdef_2026-11.zim", "cdef")
        part = self.put("zim/shelf-d_2025-12.zim")
        state = {"shelf-cdef": {"version": "2026-11", "files": ["zim/shelf-cdef_2026-11.zim"]},
                 "shelf-d": {"version": "2025-12", "files": ["zim/shelf-d_2025-12.zim"]}}
        sel = [("library", "shelf-cdef", {"dest": "zim", "version": "2026-11", "replaces": ["shelf-d"]}, [])]
        with mock.patch.object(fetch.catalog, "all_locked", return_value=[]):
            fetch._tidy(self.vault, sel, state)
        self.assertEqual(sorted(state), ["shelf-cdef"])
        self.assertFalse(os.path.exists(part))
        self.assertEqual(sorted(read_json(os.path.join(self.vault, ".genesis", "state.json"))), ["shelf-cdef"])

    def test_partial_downloads_of_files_the_catalog_dropped_are_cleared(self):
        stale = self.put("zim/shelf-d_2025-12.zim.part")
        waiting = self.put("zim/wikipedia_2026-10.zim.part")
        asked = self.put("isos/new.iso.part")
        inside = self.put("code/project/chapter.part")
        finished = self.put("zim/shelf-a_2026-11.zim")
        locked = [("library", "wikipedia", {"dest": "zim", "files": [{"filename": "wikipedia_2026-10.zim"}]}),
                  ("code", "project", {"dest": "code/project", "post": "untar", "files": [{"filename": "project.tar.gz"}]})]
        sel = [("isos", "new", {"dest": "isos"}, [{"filename": "new.iso"}])]
        with mock.patch.object(fetch.catalog, "all_locked", return_value=locked):
            fetch._tidy(self.vault, sel, {})
        self.assertFalse(os.path.exists(stale), "nothing lists this file any more")
        for path, why in ((waiting, "the catalog still lists it"), (asked, "this run asked for it by name"),
                          (inside, "a file inside an unpacked archive is content"), (finished, "a finished download")):
            self.assertTrue(os.path.isfile(path), why)


if __name__ == "__main__":
    unittest.main()
