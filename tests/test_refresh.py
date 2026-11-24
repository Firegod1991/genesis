"""A refresh must not offer anybody a file its server does not have."""

import unittest
import urllib.error
from unittest import mock

from genesis import catalog, refresh
from genesis.util import FetchError


def refuse(url, code):
    """Fail the way genesis.util.http_open fails when a server answers with this code."""
    try:
        raise urllib.error.HTTPError(url, code, "no", {}, None)
    except urllib.error.HTTPError as e:
        raise FetchError("HTTP %s for %s" % (code, url)) from e


class Servers:
    """Stands in for the network: a number is a size in bytes, a word such as "404" is what a HEAD is told."""

    def __init__(self, answers):
        self.answers = answers

    def head(self, url, headers=None):
        answer = self.answers[url]
        if isinstance(answer, str):
            refuse(url, int(answer))
        return url, {"content-length": str(answer)}


def get_refused(url, **_):
    refuse(url, 404)


def entry(*urls, **more):
    return dict({"title": "A thing", "tier": "ark", "kind": "iso", "files": [{"url": u, "filename": u.rsplit("/", 1)[-1]} for u in urls]}, **more)


class SizesTests(unittest.TestCase):
    def test_a_size_the_server_gives_is_written_down(self):
        e = entry("https://a/x.iso")
        self.assertEqual(refresh._fill_sizes(e, Servers({"https://a/x.iso": 5000})), [])
        self.assertEqual(e["files"][0]["size"], 5000)

    def test_a_file_the_server_does_not_have_is_reported(self):
        e = entry("https://a/x.iso", "https://a/y.iso")
        with mock.patch.object(refresh, "http_open", get_refused):
            gone = refresh._fill_sizes(e, Servers({"https://a/x.iso": "404", "https://a/y.iso": 7}))
        self.assertEqual(gone, ["https://a/x.iso"])
        self.assertEqual(e["files"][1]["size"], 7)

    def test_a_server_that_only_answers_get_is_believed(self):
        e = entry("https://a/x.iso")
        with mock.patch.object(refresh, "http_open", lambda url, **_: mock.Mock()):
            self.assertEqual(refresh._fill_sizes(e, Servers({"https://a/x.iso": "404"})), [])

    def test_a_server_in_trouble_is_not_a_missing_file(self):
        e = entry("https://a/x.iso")
        with mock.patch.object(refresh, "http_open", get_refused):
            self.assertEqual(refresh._fill_sizes(e, Servers({"https://a/x.iso": "503"})), [])
            self.assertEqual(refresh._fill_sizes(e, Servers({"https://a/x.iso": "403"})), [])

    def test_a_known_size_is_not_asked_for_again(self):
        e = entry("https://a/x.iso")
        e["files"][0]["size"] = 9
        self.assertEqual(refresh._fill_sizes(e, Servers({})), [])


class PackTests(unittest.TestCase):
    def refresh(self, items, answers):
        """Refresh a made up pack whose every line resolves to the files named in it."""
        pack = {"title": "Systems", "items": [dict(i, kind="iso") for i in items]}
        saved = {}

        def resolve(item, net):
            return [(item, {"version": "1", "files": [{"url": u, "filename": u.rsplit("/", 1)[-1]} for u in item["urls"]]})]

        with mock.patch.object(catalog, "load_pack", lambda name: pack), \
                mock.patch.object(catalog, "load_lock", lambda name: {}), \
                mock.patch.object(catalog, "save_lock", lambda name, new: saved.update(new)), \
                mock.patch.object(refresh, "resolve", resolve), mock.patch.object(refresh, "http_open", get_refused):
            result = refresh.refresh_pack("systems", Servers(answers))
        return result, saved

    def test_a_line_with_nothing_left_to_fetch_is_offered_to_nobody(self):
        result, saved = self.refresh([{"id": "live", "title": "Live", "tier": "ark", "urls": ["https://a/live.iso"]},
                                      {"id": "installer", "title": "Installer", "tier": "ark", "urls": ["https://a/inst.iso"]}],
                                     {"https://a/live.iso": "404", "https://a/inst.iso": 5000})
        self.assertEqual(saved["live"]["status"], "error")
        self.assertNotIn("files", saved["live"])
        self.assertIn("https://a/live.iso", saved["live"]["error"])
        self.assertEqual([i for i, _ in result["failures"]], ["live"])
        self.assertEqual(saved["installer"]["files"][0]["size"], 5000)
        self.assertNotIn("status", saved["installer"])
        self.assertIn("`live`: the server does not have https://a/live.iso", refresh.render_report([result], "2026-11-24"))

    def test_a_line_keeps_the_files_that_are_there(self):
        result, saved = self.refresh([{"id": "tool", "title": "Tool", "tier": "seed", "urls": ["https://a/arm.tgz", "https://a/x64.tgz"]}],
                                     {"https://a/arm.tgz": "404", "https://a/x64.tgz": 30})
        self.assertEqual([f["url"] for f in saved["tool"]["files"]], ["https://a/x64.tgz"])
        self.assertEqual(result["failures"], [])
        self.assertEqual(result["warnings"], [("tool", "the server does not have https://a/arm.tgz")])


class CatalogTests(unittest.TestCase):
    def test_every_system_in_the_catalog_can_be_had_by_a_plain_download(self):
        """Kali hands out its live image by torrent only. The line for it failed on every drive."""
        ids = [i["id"] for i in catalog.load_pack("isos")["items"]]
        self.assertNotIn("kali_live", ids)
        self.assertIn("kali_installer", ids)


if __name__ == "__main__":
    unittest.main()
