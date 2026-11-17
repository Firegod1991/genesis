"""Choosing between Kiwix mirrors: quick hosts are remembered, shared and fallen back through."""

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

BOOKS = fetch.KIWIX + "zim/books/"
WIKI = fetch.KIWIX + "zim/wikipedia/"


class MirrorChoiceTests(unittest.TestCase):
    """Three mirrors carry the wikipedia folder, two of them carry the books folder."""

    CARRY = {"zim/wikipedia/": ["us.example", "nl.example", "dk.example"], "zim/books/": ["nl.example", "dk.example"]}

    def setUp(self):
        self.speed = {"us.example": 40e6, "nl.example": 3e6, "dk.example": 2e6}
        self.timed = []
        fetch._mirror.clear()
        self.addCleanup(fetch._mirror.clear)

        test = self

        class Page:
            def __init__(page, url):
                page.url = url

            def __enter__(page):
                return page

            def __exit__(page, *exc):
                return False

            def read(page):
                folder, name = page.url[len(fetch.KIWIX):-len(".meta4")].rsplit("/", 1)
                urls = ["https://%s/kiwix/%s/%s" % (host, folder, name) for host in test.CARRY[folder + "/"]]
                return ("<metalink>" + "".join("<url>%s</url>" % u for u in urls) + "</metalink>").encode()

        def speed(url, seconds=3.0):
            self.timed.append(url)
            return self.speed[url.split("/")[2]]

        for patch in (mock.patch.object(fetch, "http_open", side_effect=lambda url, **kw: Page(url)),
                      mock.patch.object(fetch, "_speed", side_effect=speed)):
            patch.start()
            self.addCleanup(patch.stop)

    @staticmethod
    def host(address):
        return address.split("/")[2]

    def test_mirrors_about_as_quick_as_the_best_share_the_downloads(self):
        got = [self.host(fetch._kiwix_mirror(BOOKS + "book%d.zim" % n, 2e9)) for n in range(4)]
        self.assertEqual(got, ["nl.example", "dk.example", "nl.example", "dk.example"])
        self.assertEqual(len(self.timed), 2, "the folder is measured once")

    def test_a_mirror_far_behind_the_best_gets_nothing(self):
        got = {self.host(fetch._kiwix_mirror(WIKI + "wiki%d.zim" % n, 2e9)) for n in range(4)}
        self.assertEqual(got, {"us.example"})

    def test_a_host_that_measured_low_on_a_busy_line_keeps_its_best_reading(self):
        fetch._kiwix_mirror(WIKI + "wiki.zim", 2e9)
        self.speed = {"us.example": 40e6, "nl.example": 1e5, "dk.example": 2e6}  # nl measured while the line was busy
        got = [self.host(fetch._kiwix_mirror(BOOKS + "book%d.zim" % n, 2e9)) for n in range(2)]
        self.assertEqual(got, ["nl.example", "dk.example"], "3 MB a second was seen from nl before, and it counts")

    def test_a_download_falls_through_the_quick_mirrors_and_then_to_the_publisher(self):
        vault = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, vault, ignore_errors=True)
        dest, tried = os.path.join(vault, "zim", "book.zim"), []

        def transfer(url, part):
            tried.append(self.host(url))
            if "example" in url:
                raise FetchError("curl exit 22 for " + url)
            with open(part, "w") as f:
                f.write("zim")

        with mock.patch.object(fetch, "_transfer", side_effect=transfer):
            fetch._download(BOOKS + "book.zim", dest, 2e9)
        self.assertEqual(tried, ["nl.example", "dk.example", "download.kiwix.org"])
        self.assertTrue(os.path.isfile(dest))
        self.assertEqual(set(fetch._mirror.busy.values()), {0}, "no host is left counted as busy")

    def test_a_new_file_the_publisher_sends_to_a_stale_mirror_comes_from_one_that_has_it(self):
        vault = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, vault, ignore_errors=True)
        dest, tried = os.path.join(vault, "zim", "small.zim"), []

        def transfer(url, part):
            tried.append(self.host(url))
            if "dk.example" not in url:
                raise FetchError("curl exit 22 for " + url)  # the publisher's pick and nl have not synced yet
            with open(part, "w") as f:
                f.write("zim")

        with mock.patch.object(fetch, "_transfer", side_effect=transfer):
            fetch._download(BOOKS + "small.zim", dest, 5e6)  # too small for a speed comparison
        self.assertEqual(tried, ["download.kiwix.org", "nl.example", "dk.example"])
        self.assertTrue(os.path.isfile(dest))
        self.assertEqual(self.timed, [], "no mirror is timed for a small file")

    def test_a_file_no_mirror_has_fails_with_what_the_publisher_said(self):
        vault = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, vault, ignore_errors=True)

        def transfer(url, part):
            raise FetchError("curl exit 22 for " + url)

        with mock.patch.object(fetch, "_transfer", side_effect=transfer):
            with self.assertRaises(FetchError) as caught:
                fetch._download(BOOKS + "small.zim", os.path.join(vault, "zim", "small.zim"), 5e6)
        self.assertIn(fetch.KIWIX, str(caught.exception))
        with mock.patch.object(fetch, "_transfer", side_effect=transfer):
            with self.assertRaises(FetchError):
                fetch._download("https://example.org/file.bin", os.path.join(vault, "x", "file.bin"), 5e6)

    def test_a_finished_download_frees_its_mirror(self):
        vault = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, vault, ignore_errors=True)
        used = []

        def transfer(url, part):
            used.append(self.host(url))
            with open(part, "w") as f:
                f.write("zim")

        with mock.patch.object(fetch, "_transfer", side_effect=transfer):
            for n in range(3):
                fetch._download(BOOKS + "book%d.zim" % n, os.path.join(vault, "zim", "book%d.zim" % n), 2e9)
        self.assertEqual(used, ["nl.example"] * 3, "one at a time, the quickest of the two takes each")


if __name__ == "__main__":
    unittest.main()
