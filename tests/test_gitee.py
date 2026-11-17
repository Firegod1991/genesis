"""Sources on Gitee: the newest release of a repository, matched to each platform."""

import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from genesis import catalog, gitee, resolvers  # noqa: E402
from genesis.util import FetchError  # noqa: E402

LATEST = "https://gitee.com/api/v5/repos/Barryda/QtScrcpy/releases/latest"
DOWNLOAD = "https://gitee.com/Barryda/QtScrcpy/releases/download/v4.2.1/"
NAMES = ["QtScrcpy-mac-x64-Qt5.15.2-v4.2.1.dmg", "QtScrcpy-win-x86-v4.2.1.zip", "QtScrcpy-win-x64-v4.2.1.zip",
         "QtScrcpy-ubuntu-x64-v4.2.1.AppImage", "QtScrcpy-mac-arm64-Qt6.5.3-v4.2.1.dmg", "v4.2.1.zip", "v4.2.1.tar.gz"]
RELEASE = {"tag_name": "v4.2.1", "created_at": "2026-09-24T15:35:28+08:00",
           "assets": [{"name": n, "browser_download_url": DOWNLOAD + n} for n in NAMES]}


class Pages:
    def __init__(self, pages):
        self.pages = pages

    def json(self, url, headers=None):
        if url not in self.pages:
            raise FetchError("HTTP 404 for " + url)
        return self.pages[url]


class GiteeTests(unittest.TestCase):
    def line(self):
        return next(i for i in catalog.load_pack("tools")["items"] if i.get("id") == "qtscrcpy")

    def test_the_line_in_the_pack_picks_one_file_for_each_platform(self):
        item = self.line()
        self.assertEqual(item["source"]["type"], "gitee")
        (_, res), = resolvers.resolve(item, Pages({LATEST: RELEASE}))
        self.assertEqual((res["version"], res["date"]), ("4.2.1", "2026-09-24"))
        self.assertEqual({f["platform"]: f["filename"] for f in res["files"]},
                         {"linux-x64": "QtScrcpy-ubuntu-x64-v4.2.1.AppImage", "windows-x64": "QtScrcpy-win-x64-v4.2.1.zip",
                          "macos-arm64": "QtScrcpy-mac-arm64-Qt6.5.3-v4.2.1.dmg"})
        self.assertTrue(all(f["url"].startswith(DOWNLOAD) for f in res["files"]))
        self.assertNotIn("warnings", res)

    def test_a_platform_the_release_lacks_is_reported_and_the_rest_still_lock(self):
        short = dict(RELEASE, assets=[a for a in RELEASE["assets"] if "ubuntu" not in a["name"]])
        res = gitee.r_gitee(self.line()["source"], Pages({LATEST: short}))
        self.assertEqual(sorted(f["platform"] for f in res["files"]), ["macos-arm64", "windows-x64"])
        self.assertIn("no asset for linux-x64", res["warnings"][0])

    def test_gitee_not_answering_or_an_empty_release_fails_the_line_cleanly(self):
        src = self.line()["source"]
        for pages in ({}, {LATEST: []}, {LATEST: {"tag_name": "v4.2.1", "assets": []}}):
            with self.assertRaises(resolvers.ResolveError):
                gitee.r_gitee(src, Pages(pages))

    def test_a_named_tag_is_asked_for_by_name(self):
        url = "https://gitee.com/api/v5/repos/o/r/releases/tags/v1.0"
        res = gitee.r_gitee({"repo": "o/r", "tag": "v1.0", "asset": r"\.zip$"},
                            Pages({url: {"tag_name": "v1.0", "assets": [{"name": "a.zip", "browser_download_url": "https://gitee.com/o/r/a.zip"}]}}))
        self.assertEqual((res["version"], [f["filename"] for f in res["files"]]), ("1.0", ["a.zip"]))


if __name__ == "__main__":
    unittest.main()
