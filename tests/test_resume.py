"""Work that is already done is kept: a file that is whole, and a transfer that was cut off part way."""

import hashlib
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from genesis import fetch  # noqa: E402
from genesis.util import FetchError  # noqa: E402

GOOD = b"a whole book" * 400


class WholeFileTests(unittest.TestCase):
    """A run was stopped after a download finished and before it was recorded."""

    def setUp(self):
        self.vault = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.vault, ignore_errors=True)
        self.dest = os.path.join(self.vault, "zim", "book.zim")
        os.makedirs(os.path.dirname(self.dest))
        # Kiwix lists a size rounded up to the next KiB, so the size never settles it
        self.files = [{"url": fetch.KIWIX + "zim/books/book.zim", "filename": "book.zim",
                       "size": (len(GOOD) // 1024 + 1) * 1024, "sha256": hashlib.sha256(GOOD).hexdigest()}]
        self.downloads = []

        def download(url, dest, size=None):
            self.downloads.append(url)
            with open(dest, "wb") as f:
                f.write(GOOD)

        patch = mock.patch.object(fetch, "_download", side_effect=download)
        patch.start()
        self.addCleanup(patch.stop)

    def put(self, data, *beside):
        with open(self.dest, "wb") as f:
            f.write(data)
        for suffix in beside:
            with open(self.dest + suffix, "w") as f:
                f.write("left over")

    def run_item(self):
        return fetch._fetch_item(self.vault, "book", {"kind": "zim", "dest": "zim", "version": "1"}, self.files, None, {}, None)

    def test_a_whole_file_is_checked_and_kept_not_downloaded_again(self):
        self.put(GOOD, ".part", ".part.aria2")
        self.assertEqual(self.run_item()["files"], ["zim/book.zim"])
        self.assertEqual(self.downloads, [])
        self.assertEqual(os.listdir(os.path.dirname(self.dest)), ["book.zim"], "the second download that had begun is cleared")

    def test_a_damaged_file_is_downloaded_again(self):
        self.put(b"X" + GOOD[1:])
        self.run_item()
        self.assertEqual(len(self.downloads), 1)
        with open(self.dest, "rb") as f:
            self.assertEqual(f.read(), GOOD)

    def test_another_version_under_the_same_name_is_replaced_without_reading_it_through(self):
        self.put(GOOD + b"x" * 5000)
        with mock.patch.object(fetch, "file_hash", wraps=fetch.file_hash) as hashed:
            self.run_item()
        self.assertEqual(len(self.downloads), 1)
        self.assertEqual(hashed.call_count, 1, "only the new download is hashed")

    def test_a_missing_file_is_downloaded(self):
        self.run_item()
        self.assertEqual(len(self.downloads), 1)


class CutOffTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.folder, ignore_errors=True)
        self.part = os.path.join(self.folder, "planet.pmtiles.part")
        self.script, self.calls = [], 0

        def curl(cmd, **kw):
            self.calls += 1
            grow, rc = self.script.pop(0)
            with open(self.part, "ab") as f:
                f.write(b"x" * grow)
            return rc

        for patch in (mock.patch.object(fetch.shutil, "which", side_effect=lambda name: "/usr/bin/" + name),
                      mock.patch.object(fetch.subprocess, "call", side_effect=curl),
                      mock.patch.object(fetch, "log")):
            patch.start()
            self.addCleanup(patch.stop)

    def test_a_transfer_cut_off_part_way_goes_on_from_where_it_stopped(self):
        self.script = [(50, 92), (30, 18), (20, 0)]  # cut by the server, cut again, then the rest
        fetch._transfer("https://maps.example/planet.pmtiles", self.part)
        self.assertEqual(self.calls, 3)
        self.assertEqual(os.path.getsize(self.part), 100)

    def test_an_address_that_gives_nothing_fails_at_once(self):
        self.script = [(0, 22), (99, 0)]
        with self.assertRaises(FetchError) as caught:
            fetch._transfer("https://maps.example/planet.pmtiles", self.part)
        self.assertIn("curl exit 22", str(caught.exception))
        self.assertEqual(self.calls, 1)

    def test_a_cut_with_nothing_new_fails_and_keeps_what_there_is(self):
        self.script = [(50, 92), (0, 92), (50, 0)]
        with self.assertRaises(FetchError):
            fetch._transfer("https://maps.example/planet.pmtiles", self.part)
        self.assertEqual(self.calls, 2)
        self.assertEqual(os.path.getsize(self.part), 50, "the next run goes on from here")


if __name__ == "__main__":
    unittest.main()
