"""The biggest downloads: started first, and taken from several mirrors at once when aria2 is installed."""

import argparse
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

BIG = fetch.KIWIX + "zim/zimit/library.zim"
MIRRORS = ["https://%s.example/kiwix/zim/zimit/library.zim" % host for host in ("nl", "dk", "us")]


class SwarmTests(unittest.TestCase):
    """aria2 stands in as a command that writes the file and drops its record, as the real one does."""

    def setUp(self):
        self.vault = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.vault, ignore_errors=True)
        self.dest = os.path.join(self.vault, "zim", "library.zim")
        self.part, self.record = self.dest + ".part", self.dest + ".part.aria2"
        os.makedirs(os.path.dirname(self.dest))
        self.installed, self.listed, self.rc, self.calls, self.single = "/usr/bin/aria2c", list(MIRRORS), 0, [], []

        def aria2(cmd, **kw):
            self.calls.append(cmd)
            if self.rc == 0:
                with open(self.part, "w") as f:
                    f.write("whole")
                if os.path.exists(self.record):
                    os.remove(self.record)
            return self.rc

        def transfer(url, part):
            self.single.append((url, os.path.exists(part), os.path.exists(part + ".aria2")))
            with open(part, "w") as f:
                f.write("whole")

        for patch in (mock.patch.object(fetch.shutil, "which", side_effect=lambda name: self.installed if name == "aria2c" else None),
                      mock.patch.object(fetch, "_listed", side_effect=lambda url: list(self.listed)),
                      mock.patch.object(fetch.subprocess, "call", side_effect=aria2),
                      mock.patch.object(fetch, "_kiwix_mirror", return_value=None),
                      mock.patch.object(fetch, "_transfer", side_effect=transfer)):
            patch.start()
            self.addCleanup(patch.stop)

    def begun(self, data="pieces with holes"):
        for path in (self.part, self.record):
            with open(path, "w") as f:
                f.write(data)

    def test_a_very_big_file_comes_from_every_mirror_at_once(self):
        fetch._download(BIG, self.dest, 220e9)
        cmd = self.calls[0]
        self.assertEqual(cmd[0], "/usr/bin/aria2c")
        self.assertEqual(cmd[-3:], MIRRORS)
        for option in ("--continue=true", "--split=6", "--max-connection-per-server=2", "--out=library.zim.part",
                       "--dir=" + os.path.dirname(self.dest), "--user-agent=" + fetch.USER_AGENT):
            self.assertIn(option, cmd)
        self.assertEqual(self.single, [], "no single download beside it")
        self.assertTrue(os.path.isfile(self.dest))
        self.assertFalse(os.path.exists(self.part))

    def test_everything_else_downloads_as_before(self):
        fetch._download(BIG, self.dest, 5e9)  # a Kiwix file of ordinary size
        fetch._download("https://maps.example/planet.pmtiles", os.path.join(self.vault, "maps", "planet.pmtiles"), 140e9)
        self.listed = MIRRORS[:1]
        fetch._download(fetch.KIWIX + "zim/zimit/lonely.zim", os.path.join(self.vault, "zim", "lonely.zim"), 220e9)
        self.installed = None
        fetch._download(fetch.KIWIX + "zim/zimit/other.zim", os.path.join(self.vault, "zim", "other.zim"), 220e9)
        self.assertEqual(self.calls, [], "aria2 is for very big Kiwix files with mirrors to share, where it is installed")
        self.assertEqual(len(self.single), 4)

    def test_a_download_aria2_began_is_only_ever_continued_by_aria2(self):
        self.begun()
        fetch._download(BIG, self.dest, 5e9)  # below the size that starts one, and still its to finish
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(self.single, [])
        with open(self.dest) as f:
            self.assertEqual(f.read(), "whole")

    def test_a_begun_download_is_not_taken_for_whole_because_of_its_size(self):
        self.begun("x" * 64)  # the last piece landed first: the file is as long as it will ever be
        fetch._download(BIG, self.dest, 64)
        self.assertEqual(len(self.calls), 1, "aria2 fills the holes")
        with open(self.dest) as f:
            self.assertEqual(f.read(), "whole")

    def test_without_aria2_a_download_it_began_starts_again(self):
        self.begun()
        self.installed = None
        fetch._download(BIG, self.dest, 220e9)
        self.assertEqual(self.single, [(BIG, False, False)], "nothing of the pieces is handed to a plain download")
        self.assertFalse(os.path.exists(self.record))
        self.assertTrue(os.path.isfile(self.dest))

    def test_a_failure_fails_the_item_and_keeps_what_was_downloaded(self):
        self.begun()
        self.rc = 7
        with self.assertRaises(FetchError):
            fetch._download(BIG, self.dest, 220e9)
        self.listed = []
        with self.assertRaises(FetchError):
            fetch._download(BIG, self.dest, 220e9)
        self.assertEqual(len(self.calls), 1, "with no mirror list there is nothing to run")
        self.assertTrue(os.path.exists(self.part) and os.path.exists(self.record), "the next run continues from here")
        self.assertFalse(os.path.exists(self.dest))
        self.assertEqual(self.single, [])

    def test_tidying_takes_the_record_with_a_partial_download_nothing_waits_for(self):
        self.begun()
        with mock.patch.object(fetch.catalog, "all_locked", return_value=[]):
            fetch._tidy(self.vault, [("library", "kept", {"dest": "zim", "kind": "zim"}, [{"filename": "kept.zim"}])], {})
        self.assertFalse(os.path.exists(self.part) or os.path.exists(self.record))


class OrderTests(unittest.TestCase):
    def started(self, jobs):
        vault = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, vault, ignore_errors=True)

        def item(iid, size):
            return ("pack", iid, {"kind": "zim", "dest": "zim", "version": "1", "title": iid},
                    [{"url": "https://%s.example/%s.zim" % (iid, iid), "filename": iid + ".zim", "size": size}])

        def download(url, dest, size=None):
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            with open(dest, "w") as f:
                f.write("x" * size)

        sel = [item("small%d" % n, 10) for n in range(6)] + [item("huge", 100), item("big", 40)]
        lines = []
        args = argparse.Namespace(vault=vault, yes=True, keep_going=True, jobs=jobs)
        with mock.patch.object(fetch, "_selection", return_value=({}, sel)), \
                mock.patch.object(fetch.catalog, "all_locked", return_value=[]), \
                mock.patch.object(fetch, "_download", side_effect=download), \
                mock.patch.object(fetch, "log", side_effect=lines.append):
            self.assertEqual(fetch.fetch(args), 0)
        numbered = dict(line[1:].split("/8] pack: ") for line in lines if line.startswith("["))
        return [numbered[str(n)] for n in range(1, 9)]

    def test_an_item_bigger_than_an_even_share_of_the_run_starts_first(self):
        # 200 to download in four slots: an even share is 50, and only one item is bigger than that
        self.assertEqual(self.started(4), ["huge"] + ["small%d" % n for n in range(6)] + ["big"])

    def test_one_download_at_a_time_keeps_the_order_of_the_catalog(self):
        self.assertEqual(self.started(1), ["small%d" % n for n in range(6)] + ["huge", "big"])


if __name__ == "__main__":
    unittest.main()
