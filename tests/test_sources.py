"""Offline tests for sources that drift: GitHub releases, SourceForge folders, "latest" links."""

import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from genesis import catalog, maps, poi, resolvers  # noqa: E402
from genesis.__main__ import main  # noqa: E402
from genesis.util import FetchError  # noqa: E402

API = "https://api.github.com/repos/o/r/releases"


class Pages:
    """A stand in for Net: answers from a dict, 404 for anything else."""

    def __init__(self, pages, refused=()):
        self.pages, self.refused = pages, set(refused)

    def text(self, url, headers=None):
        if url not in self.pages:
            raise FetchError("HTTP 404 for " + url)
        data = self.pages[url]
        return data if isinstance(data, str) else json.dumps(data)

    def json(self, url, headers=None):
        return json.loads(self.text(url))

    def head(self, url, headers=None):
        if url in self.refused:
            raise FetchError("HTTP 403 for " + url)
        if url not in self.pages:
            raise FetchError("HTTP 404 for " + url)
        return url, {"content-length": str(self.pages[url])}


def release(tag, names, date="2026-11-01", updated=None):
    return {"tag_name": tag, "published_at": date + "T00:00:00Z", "assets": [
        {"name": n, "size": 5, "updated_at": (updated or date) + "T00:00:00Z",
         "browser_download_url": "https://github.com/o/r/releases/download/%s/%s" % (tag, n)} for n in names]}


class GitHubDriftTests(unittest.TestCase):
    ASSETS = {"linux-x64": r"^os_generic-x86-64-[\d.]+\.img\.xz$", "linux-arm64": r"^os_rpi5-64-[\d.]+\.img\.xz$"}

    def test_file_missing_from_a_point_release_comes_from_the_one_before(self):
        new = release("18.5", ["os_rpi5-64-18.5.img.xz"])
        old = release("18.4", ["os_rpi5-64-18.4.img.xz", "os_generic-x86-64-18.4.img.xz"], "2026-10-01")
        net = Pages({API + "/latest": new, API + "?per_page=30": [new, old]})
        res = resolvers.r_github({"repo": "o/r", "assets": self.ASSETS}, net)
        by = {f["platform"]: f["filename"] for f in res["files"]}
        self.assertEqual(res["version"], "18.5")
        self.assertEqual(by, {"linux-arm64": "os_rpi5-64-18.5.img.xz", "linux-x64": "os_generic-x86-64-18.4.img.xz"})
        self.assertIn("linux-x64 from 18.4", res["warnings"][0])

    def test_newest_release_without_builds(self):
        marker = release("v1.0.0", ["nightly-tag.txt"])
        build = release("b8123", ["tool-b8123-bin-ubuntu-x64.zip"], "2026-11-10")
        net = Pages({API + "/latest": marker, API + "?per_page=30": [marker, build]})
        src = {"repo": "o/r", "assets": {"linux-x64": r"bin-ubuntu-x64\.zip$"}}
        res = resolvers.r_github(src, net)
        self.assertEqual((res["version"], res["date"]), ("b8123", "2026-11-10"))
        self.assertIn("newest release v1.0.0 has none of the files", res["warnings"][0])
        self.assertNotIn("warnings", resolvers.r_github(dict(src, scan=True), net))
        # Builds listed above the release marked latest are found only when the line asks to scan.
        net = Pages({API + "/latest": marker, API + "?per_page=30": [build, marker]})
        self.assertEqual(resolvers.r_github(dict(src, scan=True), net)["version"], "b8123")
        with self.assertRaises(resolvers.ResolveError):
            resolvers.r_github(src, net)

    def test_borrowing_skips_previews_and_anything_newer(self):
        new = release("18.5", ["os_rpi5-64-18.5.img.xz"])
        beta = dict(release("19.0.rc1", ["os_generic-x86-64-19.0.img.xz"]), prerelease=True)
        rc = dict(release("18.5.rc2", ["os_generic-x86-64-18.5.img.xz"]), prerelease=True)
        old = release("18.4", ["os_generic-x86-64-18.4.img.xz"], "2026-10-01")
        net = Pages({API + "/latest": new, API + "?per_page=30": [beta, new, rc, old]})
        res = resolvers.r_github({"repo": "o/r", "assets": self.ASSETS}, net)
        self.assertIn("os_generic-x86-64-18.4.img.xz", [f["filename"] for f in res["files"]])

    def test_project_with_nothing_marked_latest(self):
        only = release("1.6.3", ["app-1.6.3.apk"])
        res = resolvers.r_github({"repo": "o/r", "assets": {"android": r"\.apk$"}}, Pages({API + "?per_page=30": [only]}))
        self.assertEqual(res["version"], "1.6.3")
        with self.assertRaises(resolvers.ResolveError):
            resolvers.r_github({"repo": "o/r", "assets": {"android": r"\.apk$"}}, Pages({}))

    def test_rolling_tag_is_dated_by_its_newest_upload(self):
        nightly = release("nightly", ["app_x64.zip"], "2022-11-22", updated="2026-11-13")
        res = resolvers.r_github({"repo": "o/r", "tag": "nightly", "assets": {"linux-x64": r"x64\.zip$"}},
                                 Pages({API + "/tags/nightly": nightly}))
        self.assertEqual((res["version"], res["date"]), ("2026-11-13", "2026-11-13"))

    def test_version_labels(self):
        for tag, version in (("v1.31.2", "1.31.2"), ("jq-1.8.2", "1.8.2"), ("bun-v1.4.4", "1.4.4"),
                             ("llvmorg-23.1.5", "23.1.5"), ("b8123", "b8123"), ("2.56.1.windows.1", "2.56.1.windows.1"),
                             ("1.0-beta-2", "1.0-beta-2"), ("nightly", "nightly")):
            self.assertEqual(resolvers._gh_version(tag), version)

    def test_report_names_skip_signatures_and_lead_with_likely_files(self):
        rel = release("2.8.0", ["App-2.8.0-arm64.dmg", "App-2.8.0-arm64.dmg.sig", "App-2.8.0-x86_64.AppImage",
                                "App-2.8.0-x86_64.AppImage.zsync.DIGEST", "App-2.8.0-Setup.exe", "notes.txt"])
        names = resolvers._gh_names(rel, ["windows-x64"])
        self.assertEqual(names[0], "App-2.8.0-Setup.exe")
        self.assertEqual(len(names), 4)


FEED = """<rss><channel>
<item><title><![CDATA[/stable/3.3.3-37/disk-live-3.3.3-37-amd64.zip]]></title><pubDate>Sun, 13 Sep 2026 03:10:00 UT</pubDate>
<media:content type="application/zip" url="https://x/" filesize="494074389"><media:hash algo="md5">9be17cc9b050cc1cf2f2ff5e036d60be</media:hash></media:content></item>
<item><title><![CDATA[/stable/3.3.3-37/disk-live-3.3.3-37-amd64.iso]]></title><pubDate>Sun, 13 Sep 2026 03:09:00 UT</pubDate>
<media:content type="application/octet-stream" url="https://x/" filesize="505413632"><media:hash algo="md5">0a5a4a795ce9cfb7059870290bcbc421</media:hash></media:content></item>
<item><title><![CDATA[/stable/3.1.2-9/disk-live-3.1.2-9-amd64.iso]]></title><pubDate>Tue, 16 Jan 2024 04:30:40 UT</pubDate>
<media:content type="application/octet-stream" url="https://x/" filesize="1"><media:hash algo="md5">00000000000000000000000000000000</media:hash></media:content></item>
</channel></rss>"""


class GitHubBranchTests(unittest.TestCase):
    """Data that lives in a repository with no releases: follow the newest commit on a branch."""

    TIP, OTHER = "0180b4fc418aac2b377ee648a561345745a3bedd", "8f4465358ab70b276e9770cbd7602ec370c1cb78"
    REFS = "https://github.com/o/data.git/info/refs?service=git-upload-pack"
    COMMITS = "https://api.github.com/repos/o/data/commits/"
    # What the git address answers: a length, the commit, a space, the name. The first name also lists abilities.
    ADVERT = ("001e# service=git-upload-pack\n0000005a" + TIP + " HEAD\x00multi_ack symref=HEAD:refs/heads/master\n"
              "003f" + TIP + " refs/heads/master\n0041" + TIP + " refs/heads/next-gen\n"
              "003d" + OTHER + " refs/heads/next\n0000")

    @staticmethod
    def commit(sha):
        return {"sha": sha, "commit": {"committer": {"date": "2026-11-16T09:30:00Z"}}}

    def test_default_branch_tip_becomes_one_archive_named_after_the_commit(self):
        res = resolvers.r_github_branch({"repo": "o/data"}, Pages({self.REFS: self.ADVERT}))
        self.assertEqual(res["version"], "0180b4f")
        self.assertEqual(res["files"], [{"url": "https://github.com/o/data/archive/%s.tar.gz" % self.TIP,
                                         "filename": "data-0180b4f.tar.gz"}])
        self.assertIsNone(res["date"])  # the API did not answer: the commit alone is enough

    def test_named_branch_is_matched_whole_and_without_the_length_digits(self):
        res = resolvers.r_github_branch({"repo": "o/data", "branch": "next"}, Pages({self.REFS: self.ADVERT}))
        self.assertEqual(res["version"], self.OTHER[:7])
        self.assertTrue(res["files"][0]["url"].endswith("/archive/" + self.OTHER + ".tar.gz"))

    def test_date_comes_from_the_api_when_it_answers(self):
        net = Pages({self.REFS: self.ADVERT, self.COMMITS + self.TIP: self.commit(self.TIP)})
        self.assertEqual(resolvers.r_github_branch({"repo": "o/data"}, net)["date"], "2026-11-16")

    def test_api_stands_in_when_the_git_address_will_not_answer(self):
        net = Pages({self.COMMITS + "HEAD": self.commit(self.TIP), self.COMMITS + "heads/next": self.commit(self.OTHER)})
        self.assertEqual(resolvers.r_github_branch({"repo": "o/data"}, net)["version"], "0180b4f")
        res = resolvers.r_github_branch({"repo": "o/data", "branch": "next"}, net)
        self.assertEqual((res["version"], res["date"]), (self.OTHER[:7], "2026-11-16"))

    def test_missing_branch_or_repository_is_an_error(self):
        with self.assertRaises(resolvers.ResolveError):
            resolvers.r_github_branch({"repo": "o/data", "branch": "gone"}, Pages({self.REFS: self.ADVERT}))
        with self.assertRaises(resolvers.ResolveError):
            resolvers.r_github_branch({"repo": "o/absent"}, Pages({}))

    def test_archive_lands_without_its_top_folder(self):
        import tarfile
        from genesis import fetch
        with tempfile.TemporaryDirectory() as tmp:
            src = os.path.join(tmp, "data-0180b4f", "json")
            os.makedirs(src)
            with open(os.path.join(src, "people.json"), "w") as f:
                f.write("[]")
            ball = os.path.join(tmp, "data-0180b4f.tar.gz")
            with tarfile.open(ball, "w:gz") as t:
                t.add(os.path.join(tmp, "data-0180b4f"), arcname="data-0180b4f")
            fetch._untar(ball, os.path.join(tmp, "vault", "study", "data"))
            self.assertTrue(os.path.isfile(os.path.join(tmp, "vault", "study", "data", "json", "people.json")))


class SourceForgeTests(unittest.TestCase):
    def test_folder_feed_finds_the_newest_upload(self):
        net = Pages({"https://sourceforge.net/projects/disk/rss?path=/stable&limit=100": FEED})
        res = resolvers.r_sourceforge({"project": "disk", "path": "/stable", "pattern": r"amd64\.iso$"}, net)
        f = res["files"][0]
        self.assertEqual((res["version"], res["date"]), ("3.3.3-37", "2026-09-13"))
        self.assertEqual(f["url"], "https://downloads.sourceforge.net/project/disk/stable/3.3.3-37/disk-live-3.3.3-37-amd64.iso")
        self.assertEqual((f["size"], f["md5"]), (505413632, "0a5a4a795ce9cfb7059870290bcbc421"))

    def test_folder_feed_per_platform(self):
        net = Pages({"https://sourceforge.net/projects/disk/rss?path=/stable&limit=100": FEED})
        res = resolvers.r_sourceforge({"project": "disk", "path": "/stable", "files": {
            "windows-x64": r"amd64\.zip$", "macos-arm64": r"\.dmg$"}}, net)
        self.assertEqual([f["platform"] for f in res["files"]], ["windows-x64"])
        self.assertEqual(res["warnings"], ["no file for macos-arm64"])

    def test_default_download_per_operating_system(self):
        best = {"release": {"filename": "/w-3.0.0-rc1/w-3.0.0-rc1.tgz", "date": "2025-09-14 08:51:43"},
                "platform_releases": {
                    "windows": {"filename": "/w-3.0.0-rc1/w-3.0.0-rc1-win64.exe", "date": "2025-09-14 08:51:17",
                                "bytes": 7, "md5sum": "a" * 32},
                    "mac": {"filename": "/other/other-2.0.dmg", "date": "2026-07-30 15:22:24"}}}
        net = Pages({"https://sourceforge.net/projects/w/best_release.json": best})
        res = resolvers.r_sourceforge({"project": "w", "pattern": "w-", "version_regex": r"(\d+\.\d+\.\d+(?:-rc\d+)?)",
                                       "platforms": {"windows-x64": "windows", "macos-arm64": "mac"}}, net)
        self.assertEqual(res["version"], "3.0.0-rc1")
        self.assertEqual([(f["platform"], f["size"]) for f in res["files"]], [("windows-x64", 7)])
        single = resolvers.r_sourceforge({"project": "w", "pattern": "w-"}, net)
        self.assertEqual(single["files"][0]["filename"], "w-3.0.0-rc1.tgz")
        self.assertNotIn("platform", single["files"][0])


class RedirectTests(unittest.TestCase):
    SRC = {"url": "https://dl.invalid/download?stream=next", "version_regex": r"next-(\d{8})",
           "files": {"windows-x64": "tool-next-{version}-installer.exe", "any": "tool-{version}-py3-none-any.whl",
                     "macos-arm64": "tool-next-{version}.app.zip"}}
    BASE = "https://dl.invalid/builds/next-20261113/"

    def setUp(self):
        self.real = resolvers._redirect_target
        resolvers._redirect_target = lambda url: self.BASE.rstrip("/")

    def tearDown(self):
        resolvers._redirect_target = self.real

    def test_files_are_named_after_the_folder_and_checked(self):
        net = Pages({self.BASE + "tool-next-20261113-installer.exe": 31000000},
                    refused=[self.BASE + "tool-20261113-py3-none-any.whl"])
        res = resolvers.r_redirect(self.SRC, net)
        self.assertEqual((res["version"], res["date"]), ("20261113", "2026-11-13"))
        got = {f.get("platform", "any"): f for f in res["files"]}
        self.assertEqual(sorted(got), ["any", "windows-x64"], "a file the server says is absent is dropped")
        self.assertEqual(got["windows-x64"]["size"], 31000000)
        self.assertEqual(got["any"]["url"], self.BASE + "tool-20261113-py3-none-any.whl")
        self.assertIn("would not confirm tool-20261113-py3-none-any.whl", res["warnings"][0])

    def test_nothing_there_is_an_error(self):
        with self.assertRaises(resolvers.ResolveError):
            resolvers.r_redirect(self.SRC, Pages({}))
        resolvers._redirect_target = lambda url: "https://dl.invalid/elsewhere"
        with self.assertRaises(resolvers.ResolveError):
            resolvers.r_redirect(self.SRC, Pages({}))


class PlanetBuildTests(unittest.TestCase):
    """Protomaps drops a daily build after about a week: the map tool must not trust an old address."""

    BUILDS = "https://build-metadata.protomaps.dev/builds.json"
    OLD = "https://build.protomaps.com/20261101.pmtiles"

    def setUp(self):
        self._load_lock = catalog.load_lock
        catalog.load_lock = lambda pack: {"planet_basemap": {"files": [{"url": self.OLD}]}}

    def tearDown(self):
        catalog.load_lock = self._load_lock

    def test_newest_build_wins_over_the_catalog(self):
        net = Pages({self.BUILDS: [{"key": "20260811.pmtiles"}, {"key": "20261114.pmtiles", "size": 5}]})
        with tempfile.TemporaryDirectory() as vault:
            self.assertEqual(maps.planet_source(vault, net), "https://build.protomaps.com/20261114.pmtiles")

    def test_catalog_build_is_the_fallback_offline(self):
        with tempfile.TemporaryDirectory() as vault:
            self.assertEqual(maps.planet_source(vault, Pages({})), self.OLD)
            self.assertEqual(maps.planet_source(vault, Pages({self.BUILDS: []})), self.OLD)

    def test_local_planet_file_needs_no_network(self):
        with tempfile.TemporaryDirectory() as vault:
            os.makedirs(os.path.join(vault, "maps"))
            path = os.path.join(vault, "maps", "planet.pmtiles")
            open(path, "w").close()
            self.assertEqual(maps.planet_source(vault, Pages({})), path)


class CommandLineTests(unittest.TestCase):
    """A source that is down, or a tool that stops, is reported in one line."""

    def run_build(self, failure):
        def fail(region, vault):
            raise failure
        real, err = poi.fetch_region, io.StringIO()
        poi.fetch_region = fail
        try:
            with tempfile.TemporaryDirectory() as vault, contextlib.redirect_stderr(err):
                code = main(["poi", "build", "--region", "nowhere", "--vault", vault])
        finally:
            poi.fetch_region = real
        return code, err.getvalue()

    def check(self, failure, line):
        # Only the message is checked: the interpreter may add warnings of its own to the same stream.
        code, said = self.run_build(failure)
        self.assertEqual(code, 1)
        self.assertIn(line, said.splitlines())
        self.assertNotIn("Traceback (most recent call last)", said)

    def test_refused_download(self):
        self.check(FetchError("curl exit 35 for https://example.invalid/nowhere.osm.pbf"),
                   "genesis: curl exit 35 for https://example.invalid/nowhere.osm.pbf")

    def test_tool_that_stops(self):
        self.check(subprocess.CalledProcessError(3, ["/usr/bin/osmium", "export"]),
                   "genesis: /usr/bin/osmium stopped with exit code 3")


if __name__ == "__main__":
    unittest.main()
