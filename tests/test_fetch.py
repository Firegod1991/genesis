"""How a fetch behaves: several downloads at once, and models that land inside the vault."""

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from genesis import fetch, seal  # noqa: E402
from genesis.util import FetchError, file_hash, read_json, write_json  # noqa: E402

STUB = [sys.executable, os.path.join(ROOT, "tests", "stub_ollama.py")]


class Registry(BaseHTTPRequestHandler):
    """Stands in for registry.ollama.ai: manifests and the files they name, by SHA 256."""
    blobs, manifests, asked = {}, {}, []

    def log_message(self, fmt, *args):
        pass

    def do_GET(self):
        Registry.asked.append(self.path)
        body = None
        if "/manifests/" in self.path:
            body = Registry.manifests.get(self.path)
        elif "/blobs/sha256:" in self.path:
            body = Registry.blobs.get(self.path.rsplit("/", 1)[1])
        if body is None:
            self.send_response(404)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def publish(lib, tag, layers, corrupt=None):
    """Put a model in the stand in registry. Returns its manifest as published."""
    files = [b'{"model_format":"gguf"}'] + layers
    digests = ["sha256:" + hashlib.sha256(b).hexdigest() for b in files]
    for d, b in zip(digests, files):
        Registry.blobs[d] = corrupt if corrupt and b == files[-1] else b
    manifest = {"schemaVersion": 2, "config": {"digest": digests[0], "size": len(files[0])},
                "layers": [{"digest": d, "size": len(b)} for d, b in zip(digests[1:], files[1:])]}
    raw = json.dumps(manifest).encode()
    Registry.manifests["/v2/%s/manifests/%s" % (lib, tag)] = raw
    return raw


class ModelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Registry)
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()
        cls.base = "http://127.0.0.1:%d" % cls.httpd.server_address[1]

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()

    def setUp(self):
        self.vault = tempfile.mkdtemp()
        self.store = os.path.join(self.vault, "models", "ollama", "models")
        self.models = fetch.Models(self.vault)
        self.addCleanup(shutil.rmtree, self.vault, ignore_errors=True)
        self.addCleanup(self.models.close)

    def world(self, stack=False, explicit=None):
        """The stack running or not, a server named by the owner or not, and the stand in registry."""
        env = {k: v for k, v in os.environ.items() if k not in ("GENESIS_OLLAMA",) and "proxy" not in k.lower()}
        env["GENESIS_REGISTRY"] = self.base
        if explicit:
            env["GENESIS_OLLAMA"] = explicit
        for patch in (mock.patch.object(fetch, "_stack_ollama_running", return_value=stack),
                      mock.patch.dict(os.environ, env, clear=True)):
            patch.start()
            self.addCleanup(patch.stop)

    def test_a_model_comes_from_the_registry_with_no_ollama_at_all(self):
        self.world()
        raw = publish("library/tiny", "1", [b"weights" * 100, b"a license"])
        entry = {"kind": "model", "model": "tiny:1", "version": "abc"}
        got = fetch._fetch_item(self.vault, "tiny", entry, [], None, {}, self.models)
        self.assertEqual(got, {"version": "abc", "files": [], "model": "tiny:1"})
        manifest = os.path.join(self.store, "manifests", "registry.ollama.ai", "library", "tiny", "1")
        with open(manifest, "rb") as f:
            self.assertEqual(f.read(), raw, "the manifest exactly as the registry gave it, where Ollama looks")
        for layer in [json.loads(raw)["config"]] + json.loads(raw)["layers"]:
            path = os.path.join(self.store, "blobs", layer["digest"].replace(":", "-"))
            self.assertEqual(file_hash(path, "sha256"), layer["digest"][7:])
        self.assertFalse([n for n in os.listdir(os.path.join(self.store, "blobs")) if n.endswith(".part")])

    def test_a_namespaced_model_and_a_second_pull_downloads_nothing(self):
        self.world()
        publish("huihui_ai/small-abliterated", "4b", [b"other weights" * 50])
        self.assertTrue(self.models.pull("huihui_ai/small-abliterated:4b"))
        self.assertTrue(os.path.isfile(os.path.join(
            self.store, "manifests", "registry.ollama.ai", "huihui_ai", "small-abliterated", "4b")))
        del Registry.asked[:]
        self.assertTrue(self.models.pull("huihui_ai/small-abliterated:4b"))
        self.assertEqual([p for p in Registry.asked if "/blobs/" in p], [], "files already checked are kept")

    def test_a_file_that_does_not_match_its_sha256_fails_and_leaves_no_manifest(self):
        self.world()
        publish("library/bent", "1", [b"good weights"], corrupt=b"bad weights!")
        with self.assertRaises(FetchError):
            self.models.pull("bent:1")
        self.assertFalse(os.path.exists(os.path.join(self.store, "manifests", "registry.ollama.ai", "library", "bent")))

    def test_a_model_the_registry_does_not_have_fails_the_item(self):
        self.world()
        with self.assertRaises(FetchError):
            self.models.pull("missing:1")

    def test_the_stack_does_the_pulling_while_it_runs(self):
        self.world(stack=True)
        self.assertEqual(self.models.start(), fetch.STACK_OLLAMA)

    def test_a_server_named_by_the_owner_pulls(self):
        port = socket_port()
        proc = subprocess.Popen(STUB, env=dict(os.environ, OLLAMA_HOST="127.0.0.1:%d" % port, OLLAMA_MODELS=self.vault))
        self.addCleanup(proc.wait)
        self.addCleanup(proc.kill)
        self.world(stack=True, explicit="127.0.0.1:%d/" % port)
        self.assertEqual(self.models.start(), "http://127.0.0.1:%d" % port)
        for _ in range(40):
            try:
                if self.models.pull("tiny:1"):
                    break
            except FetchError:
                raise
            time.sleep(0.25)
        self.assertTrue(os.path.isfile(os.path.join(self.vault, "tiny_1")), "pulled by that server")
        with self.assertRaises(FetchError):
            self.models.pull("missing:1")


def socket_port():
    import socket
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


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


class CarriedTests(unittest.TestCase):
    """What a drive carries of each item: a build for its machines, or the source where there is none."""
    LINUX = ["linux-arm64", "linux-x64", "android"]
    FLDIGI = {"kind": "archive", "dest": "radio", "version": "4.2", "title": "Fldigi", "files": [
        {"filename": "fldigi_setup.exe", "platform": "windows-x64", "url": "https://example.invalid/fldigi_setup.exe"},
        {"filename": "fldigi.dmg", "platform": "macos-arm64", "url": "https://example.invalid/fldigi.dmg"},
        {"filename": "fldigi.tar.gz", "platform": "source", "url": "https://example.invalid/fldigi.tar.gz"}]}
    RUFUS = {"kind": "tool", "dest": "tools", "version": "4.15", "title": "Rufus", "files": [
        {"filename": "rufus.exe", "platform": "windows-x64", "url": "https://example.invalid/rufus.exe"}]}

    def setUp(self):
        self.vault = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.vault, ignore_errors=True)
        self.args = argparse.Namespace(vault=self.vault, yes=True, keep_going=True, jobs=2)

    def names(self, entry, platforms):
        return [f["filename"] for f in fetch.catalog.wanted_files(entry, platforms)]

    def selection(self, platforms):
        return [("radio", "fldigi", self.FLDIGI, fetch.catalog.wanted_files(self.FLDIGI, platforms)),
                ("tools", "rufus", self.RUFUS, fetch.catalog.wanted_files(self.RUFUS, platforms))]

    def run_fetch(self, sel):
        def download(url, dest, size=None):
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            with open(dest, "w") as f:
                f.write("hello")
        with mock.patch.object(fetch, "_selection", return_value=({}, sel)), \
                mock.patch.object(fetch, "_download", side_effect=download):
            code = fetch.fetch(self.args)
        return code, read_json(os.path.join(self.vault, ".genesis", "state.json"), {})

    def test_a_program_with_no_build_for_the_drive_comes_as_its_source(self):
        self.assertEqual(self.names(self.FLDIGI, self.LINUX), ["fldigi.tar.gz"])
        self.assertEqual(self.names(self.FLDIGI, ["windows-x64"]), ["fldigi_setup.exe"], "a build is enough")
        self.assertEqual(self.names(self.RUFUS, self.LINUX), [], "no source listed: nothing to bring")
        both = {"files": [{"filename": "tool-linux-arm64", "platform": "linux-arm64"},
                          {"filename": "manual.pdf"}, {"filename": "tool.tar.gz", "platform": "source"}]}
        self.assertEqual(self.names(both, self.LINUX), ["tool-linux-arm64", "manual.pdf"])
        self.assertEqual(self.names(both, ["windows-x64"]), ["manual.pdf", "tool.tar.gz"])
        self.assertEqual(self.names(both, self.LINUX + ["source"]), ["tool-linux-arm64", "manual.pdf", "tool.tar.gz"],
                         "asked for by name, the source comes beside the build, once")

    def test_only_what_landed_on_the_drive_is_recorded(self):
        code, state = self.run_fetch(self.selection(self.LINUX))
        self.assertEqual(code, 0)
        self.assertEqual(state["fldigi"]["files"], ["radio/fldigi.tar.gz"])
        self.assertNotIn("rufus", state, "a program for another system leaves no record")
        self.assertEqual(os.listdir(os.path.join(self.vault, "radio")), ["fldigi.tar.gz"])

    def test_empty_records_from_an_older_genesis_are_looked_at_again(self):
        write_json(os.path.join(self.vault, ".genesis", "state.json"), {
            "fldigi": {"version": "4.2", "files": [], "pack": "radio"},
            "rufus": {"version": "4.15", "files": [], "pack": "tools"},
            "tiny": {"version": "abc", "files": [], "model": "tiny:1", "pack": "ai"}})
        self.assertEqual(sorted(fetch._state(self.vault)), ["tiny"], "a model keeps its record: it lives in the model store")
        code, state = self.run_fetch(self.selection(self.LINUX))
        self.assertEqual(code, 0)
        self.assertEqual(state["fldigi"]["files"], ["radio/fldigi.tar.gz"], "fetched now that the source counts")
        self.assertEqual(sorted(state), ["fldigi", "tiny"])

    def test_plan_and_status_leave_out_what_this_drive_cannot_carry(self):
        sel, out = self.selection(self.LINUX), []
        with mock.patch.object(fetch, "_selection", return_value=({"tier": "ark", "langs": ["eng"], "platforms": self.LINUX}, sel)), \
                mock.patch("builtins.print", lambda *a: out.append(" ".join(map(str, a)))):
            fetch.status(self.args)
            fetch.plan(self.args)
        said = "\n".join(out)
        self.assertRegex(said, r"radio\s+0\s+0\s+1\b", "the source is still to fetch")
        self.assertNotIn("tools", said, "Rufus is for Windows: neither missing nor planned")

    def test_the_checksum_of_a_download_is_kept_in_its_record(self):
        body = b"a book"
        want = __import__("hashlib").sha256(body).hexdigest()
        entry = {"kind": "zim", "dest": "zim", "version": "1"}
        files = [{"filename": "book.zim", "url": "https://example.invalid/book.zim", "sha256": want, "size": len(body)}]

        def download(url, dest, size=None):
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            with open(dest, "wb") as f:
                f.write(body)
        with mock.patch.object(fetch, "_download", side_effect=download):
            got = fetch._fetch_item(self.vault, "book", entry, files, None, {}, None)
        path = os.path.join(self.vault, "zim", "book.zim")
        self.assertEqual(got["sums"], {"zim/book.zim": {"bytes": len(body), "mtime": int(os.path.getmtime(path)), "sha256": want}})
        plain = [{"filename": "note.txt", "url": "https://example.invalid/note.txt", "size": len(body)}]
        with mock.patch.object(fetch, "_download", side_effect=download):
            got = fetch._fetch_item(self.vault, "note", dict(entry, dest="docs"), plain, None, {}, None)
        self.assertNotIn("sums", got, "nothing is kept for a file that was only checked by its size")


class VerifyTests(unittest.TestCase):
    def setUp(self):
        self.vault = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.vault, ignore_errors=True)
        files, state = [], {}
        for n in range(12):
            path = os.path.join(self.vault, "zim", "book%d.zim" % n)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w") as f:
                f.write("book %d" % n)
            sums = {"sha256": file_hash(path)} if n % 2 else {"sha256_url": "https://example.invalid/book%d.sha256" % n}
            files.append(("zim", "book%d" % n, {"version": "1", "files": [dict(sums, filename="book%d.zim" % n)]}))
            state["book%d" % n] = {"version": "1", "files": ["zim/book%d.zim" % n]}
        write_json(os.path.join(self.vault, ".genesis", "state.json"), state)
        patch = mock.patch.object(fetch.catalog, "all_locked", return_value=files)
        patch.start()
        self.addCleanup(patch.stop)

    def run_verify(self, online):
        def text(net, url, headers=None):
            if not online:
                raise FetchError("no route to " + url)
            number = url.rsplit("book", 1)[1].split(".")[0]
            return file_hash(os.path.join(self.vault, "zim", "book%s.zim" % number)) + "  book%s.zim" % number
        out = []
        with mock.patch.object(fetch.Net, "text", text), mock.patch("builtins.print", lambda *a: out.append(" ".join(map(str, a)))):
            code = fetch.verify(argparse.Namespace(vault=self.vault, jobs=4))
        return code, "\n".join(out)

    def test_every_file_is_compared_when_the_publishers_answer(self):
        code, said = self.run_verify(online=True)
        self.assertEqual(code, 0)
        self.assertIn("12 installed items, 12 files re hashed, 0 problems", said)

    def test_with_no_connection_nothing_is_called_bad_that_could_not_be_compared(self):
        code, said = self.run_verify(online=False)
        self.assertEqual(code, 0)
        self.assertIn("12 installed items, 6 files re hashed, 0 problems", said)
        self.assertIn("6 files had nothing to be compared with", said)
        self.assertIn("verify --sealed", said)

    def test_a_changed_file_is_still_caught(self):
        with open(os.path.join(self.vault, "zim", "book3.zim"), "w") as f:
            f.write("book X")
        code, said = self.run_verify(online=False)
        self.assertEqual(code, 1)
        self.assertIn("BAD book3: sha256 mismatch", said)


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

    def vault(self):
        vault = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, vault, ignore_errors=True)
        return vault

    def put(self, vault, rel, text, when=None):
        path = os.path.join(vault, *rel.split("/"))
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            f.write(text)
        if when:
            os.utime(path, (when, when))
        return path

    def sealed(self, vault, quick=False):
        """Seal, and say which files were read through to do it."""
        read, real = [], seal.file_hash

        def through(path, *a):
            read.append(os.path.relpath(path, vault).replace(os.sep, "/"))
            return real(path, *a)
        with mock.patch.object(seal, "log"), mock.patch.object(seal.shutil, "which", return_value=None), \
                mock.patch.object(seal.shutil, "make_archive"), mock.patch.object(seal, "file_hash", side_effect=through):
            seal.seal(argparse.Namespace(vault=vault, quick=quick))
        return read_json(os.path.join(vault, "restore", "MANIFEST.json"))["files"], sorted(r for r in read if not r.startswith("restore/"))

    def test_a_quick_seal_reads_only_what_is_new_or_changed(self):
        vault = self.vault()
        self.put(vault, "zim/old.zim", "an old book", when=1000000000)
        self.put(vault, "zim/changed.zim", "first words", when=1000000000)
        first, read = self.sealed(vault)
        self.assertEqual(read, ["zim/changed.zim", "zim/old.zim"], "a full seal reads everything")
        self.assertEqual(first["zim/old.zim"]["mtime"], 1000000000)
        self.put(vault, "zim/changed.zim", "other words")
        got = self.put(vault, "zim/downloaded.zim", "proved when it was downloaded")
        self.put(vault, "zim/stranger.zim", "nobody vouches for this one")
        write_json(os.path.join(vault, ".genesis", "state.json"), {"book": {"version": "1", "files": ["zim/downloaded.zim"], "sums": {
            "zim/downloaded.zim": {"bytes": os.path.getsize(got), "mtime": int(os.path.getmtime(got)), "sha256": file_hash(got)}}}})
        second, read = self.sealed(vault, quick=True)
        self.assertEqual(read, ["zim/changed.zim", "zim/stranger.zim"])
        full, read = self.sealed(vault)
        self.assertEqual(len(read), 4)
        self.assertEqual({k: v["sha256"] for k, v in second.items() if k.startswith("zim/")},
                         {k: v["sha256"] for k, v in full.items() if k.startswith("zim/")}, "and arrives at the same seal")
        self.assertEqual(seal.check(argparse.Namespace(vault=vault)), 0)

    def test_a_quick_seal_never_hides_damage_from_the_full_check(self):
        vault = self.vault()
        path = self.put(vault, "zim/book.zim", "every word in place", when=1000000000)
        self.sealed(vault)
        self.put(vault, "zim/book.zim", "every word in plaze", when=1000000000)  # the same size and time: bit rot
        again, read = self.sealed(vault, quick=True)
        self.assertEqual(read, [], "a quick seal takes an untouched file at its word")
        self.assertNotEqual(again["zim/book.zim"]["sha256"], file_hash(path))
        with mock.patch("builtins.print"):
            self.assertEqual(seal.check(argparse.Namespace(vault=vault)), 1, "the check reads every file and finds it")

    def test_a_seal_from_before_times_were_kept_still_vouches_for_untouched_files(self):
        vault = self.vault()
        old = self.put(vault, "zim/old.zim", "an old book", when=1000000000)
        new = self.put(vault, "zim/new.zim", "came later")
        last = os.path.join(vault, "restore", "MANIFEST.json")
        write_json(last, {"sealed": "2026-11-17", "files": {"zim/old.zim": {"bytes": os.path.getsize(old), "sha256": file_hash(old)},
                                                            "zim/new.zim": {"bytes": os.path.getsize(new), "sha256": "0" * 64}}})
        os.utime(last, (1500000000, 1500000000))
        again, read = self.sealed(vault, quick=True)
        self.assertEqual(read, ["zim/new.zim"], "touched after that seal, so it is read again")
        self.assertEqual(again["zim/new.zim"]["sha256"], file_hash(new))
        self.assertEqual(again["zim/old.zim"], {"bytes": os.path.getsize(old), "mtime": 1000000000, "sha256": file_hash(old)})


if __name__ == "__main__":
    unittest.main()
