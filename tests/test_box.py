"""The box with no Docker: what the drive carries is unpacked once, then started with nothing but Python."""

import argparse
import io
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from genesis import box, catalog, seal, util  # noqa: E402
from genesis.util import read_json  # noqa: E402


def archive(path, members, mode="w:gz"):
    with tarfile.open(path, mode) as t:
        for name, data in members:
            info = tarfile.TarInfo(name)
            if data is None:  # a link
                info.type, info.linkname = tarfile.SYMTYPE, "libggml.so.1"
                t.addfile(info)
            else:
                info.size, info.mode = len(data), 0o755
                t.addfile(info, io.BytesIO(data))


class BoxTests(unittest.TestCase):
    def setUp(self):
        self.drive = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.drive, ignore_errors=True)
        self.vault = os.path.join(self.drive, "vault")
        for sub in ("tools", "zim", os.path.join("models", "ollama", "models")):
            os.makedirs(os.path.join(self.vault, sub))
        self.here = catalog.host_platform()

    def kiwix(self, machine="linux-aarch64"):
        top = "kiwix-tools_%s-3.8.2" % machine
        archive(os.path.join(self.vault, "tools", top + ".tar.gz"),
                [(top + "/kiwix-serve", b"serve"), (top + "/kiwix-manage", b"manage")])

    def test_the_kiwix_server_on_the_drive_is_unpacked_for_the_machine_it_is_for(self):
        self.kiwix()
        self.assertEqual(box.unpack_programs(self.vault), ["the Kiwix server for linux-arm64"])
        exe = os.path.join(self.vault, "runtime", "linux-arm64", "kiwix", "kiwix-serve")
        with open(exe, "rb") as f:
            self.assertEqual(f.read(), b"serve")
        if os.name != "nt":
            self.assertTrue(os.access(exe, os.X_OK))
        self.assertEqual(box.unpack_programs(self.vault), [], "done once, not on every start")

    def test_an_archive_cannot_write_outside_its_folder(self):
        archive(os.path.join(self.vault, "tools", "kiwix-tools_linux-x86_64-9.tar.gz"), [("top/../../../evil", b"x")])
        with self.assertRaises(ValueError):
            box.unpack_programs(self.vault)
        self.assertFalse(os.path.exists(os.path.join(self.drive, "evil")))

    @unittest.skipUnless(shutil.which("zstd") and os.name != "nt", "needs the zstd program")
    def test_ollama_is_unpacked_with_the_links_its_libraries_use(self):
        plain = os.path.join(self.drive, "ollama.tar")
        archive(plain, [("bin/ollama", b"ollama"), ("lib/ollama/libggml.so.1", b"lib"), ("lib/ollama/libggml.so", None)], "w")
        subprocess.check_call(["zstd", "-q", plain, "-o", os.path.join(self.vault, "tools", "ollama-linux-arm64.tar.zst")])
        self.assertEqual(box.unpack_programs(self.vault), ["Ollama for linux-arm64"])
        into = os.path.join(self.vault, "runtime", "linux-arm64", "ollama")
        self.assertTrue(os.access(os.path.join(into, "bin", "ollama"), os.X_OK))
        self.assertEqual(os.readlink(os.path.join(into, "lib", "ollama", "libggml.so")), "libggml.so.1")

    def program(self, kind, name):
        folder = os.path.join(self.vault, "runtime", self.here, kind)
        os.makedirs(folder, exist_ok=True)
        path = os.path.join(folder, name + (".exe" if os.name == "nt" else ""))
        open(path, "w").close()
        return path

    def test_only_what_the_drive_can_run_here_is_started(self):
        with mock.patch.object(box.shutil, "which", return_value=None):
            self.assertEqual([(name, env) for name, _, _, env in box.plan(self.vault)], [("dashboard", {"GENESIS_BARE": "1"})])
            reader, book = self.program("kiwix", "kiwix-serve"), os.path.join(self.vault, "zim", "wikipedia.zim")
            open(book, "w").close()
            open(book + ".part", "w").close()
            ollama = self.program("ollama", "ollama")
            plan = {name: (port, cmd, env) for name, port, cmd, env in box.plan(self.vault)}
        self.assertEqual(plan["library"][1], [reader, "--port", "8081", book], "whole books only")
        self.assertEqual(plan["assistant"][1], [ollama, "serve"])
        self.assertEqual(plan["assistant"][2]["OLLAMA_MODELS"], os.path.join(self.vault, "models", "ollama", "models"))
        self.assertEqual(plan["assistant"][2]["OLLAMA_NOPRUNE"], "1", "the model server deletes nothing from the drive")

    def test_the_reader_starts_from_a_list_of_the_books_once_one_is_written(self):
        reader, manage = self.program("kiwix", "kiwix-serve"), self.program("kiwix", "kiwix-manage")
        shelf = [os.path.join(self.vault, "zim", name) for name in ("a.zim", "b.zim", "bad.zim")]
        for book in shelf:
            open(book, "w").close()
        asked = []

        def kiwix_manage(cmd, **kw):
            asked.append(cmd)
            if any(path.endswith("bad.zim") for path in cmd[3:]):
                return 1
            with open(cmd[1], "a") as f:
                f.write("".join(os.path.basename(path) + " " for path in cmd[3:]))
            return 0

        listed = os.path.join(self.vault, ".genesis", "library.xml")
        with mock.patch.object(box.shutil, "which", return_value=None), mock.patch.object(box, "log") as said, \
                mock.patch.object(box.subprocess, "call", side_effect=kiwix_manage):
            self.assertEqual(box.list_books(self.vault), 2)
            self.assertEqual(asked[0], [manage, listed + ".tmp", "add"] + shelf)
            with open(listed) as f:
                self.assertEqual(f.read(), "a.zim b.zim ", "a damaged book does not keep the others off the list")
            self.assertIn("bad.zim", said.call_args[0][0])
            plan = {name: cmd for name, _, cmd, _ in box.plan(self.vault)}
            self.assertEqual(plan["library"], [reader, "--port", "8081", "--library", listed])
            before = len(asked)
            self.assertEqual(box.list_books(self.vault), 2)
            self.assertEqual(len(asked), before, "written once, not on every start")
            new = os.path.join(self.vault, "zim", "c.zim")
            open(new, "w").close()
            plan = {name: cmd for name, _, cmd, _ in box.plan(self.vault)}
        self.assertEqual(plan["library"], [reader, "--port", "8081"] + shelf[:2] + [shelf[2], new], "a new book is not left out")

    def test_start_brings_up_what_is_not_running_and_says_what_is_missing(self):
        self.program("kiwix", "kiwix-serve")
        open(os.path.join(self.vault, "zim", "wikipedia.zim"), "w").close()
        up, said, run = {8081}, [], os.path.join(self.drive, "run")
        os.makedirs(run)

        def spawn(cmd, env, logname):
            up.add(int(env["PORT"]))
            return 4000 + len(up)

        quiet = {k: v for k, v in os.environ.items() if k not in ("DISPLAY", "WAYLAND_DISPLAY")}
        with mock.patch.object(box, "_spawn", side_effect=spawn), mock.patch.object(box, "_listening", side_effect=lambda p: p in up), \
                mock.patch.object(box, "_run_dir", return_value=run), mock.patch.object(box, "log", side_effect=said.append), \
                mock.patch.object(box.shutil, "which", return_value=None), mock.patch.object(box.webbrowser, "open"), \
                mock.patch.dict(os.environ, quiet, clear=True):
            self.assertEqual(box.start(argparse.Namespace(vault=self.vault, no_browser=True)), 0)
        self.assertEqual(sorted(read_json(os.path.join(run, "box.json"))), ["dashboard"], "the reader was already running")
        self.assertIn("library is already running on port 8081", said)
        self.assertTrue(any(line.startswith("not started: the assistant") for line in said))
        self.assertIn("Genesis is up: http://localhost:8080/", said)

    def test_the_drive_gets_a_front_door_and_its_folders_their_pictures(self):
        code = os.path.join(self.drive, "genesis")
        icons = os.path.join(code, "dashboard", "static", "icons")
        os.makedirs(icons)
        for name in ("book", "genesis", "seal", "gear", "tools", "assistant"):
            open(os.path.join(icons, name + ".svg"), "w").close()
        with mock.patch.object(box, "ROOT", code):
            self.assertEqual(box.dress(self.vault), 3)
        with open(os.path.join(self.vault, "zim", ".directory")) as f:
            self.assertEqual(f.read(), "[Desktop Entry]\nIcon=%s\n" % os.path.join(icons, "book.svg"))
        with open(os.path.join(self.drive, "Start Genesis.desktop")) as f:
            door = f.read()
        self.assertIn("Icon=%s\n" % os.path.join(icons, "genesis.svg"), door)
        # The launcher's own path comes as an argument of its own, so a space in it, as in its name, breaks nothing.
        self.assertIn('Exec=sh -c \'exec sh "$(dirname "$1")/genesis/bin/genesis" start\' genesis %k\n', door)
        self.assertTrue(os.path.isfile(os.path.join(self.drive, "Start Genesis.sh")))

    def test_the_seal_leaves_out_what_is_not_finished_and_what_each_machine_rewrites(self):
        for rel in ("zim/whole.zim", "zim/half.zim.part", "zim/half.zim.part.aria2", "zim/.directory"):
            with open(os.path.join(self.vault, *rel.split("/")), "w") as f:
                f.write("x")
        with mock.patch.object(seal, "log"), mock.patch.object(seal.shutil, "which", return_value=None), \
                mock.patch.object(seal.shutil, "make_archive"):
            seal.seal(argparse.Namespace(vault=self.vault))
        listed = read_json(os.path.join(self.vault, "restore", "MANIFEST.json"))["files"]
        self.assertEqual(sorted(listed), ["zim/whole.zim"])


class OneDiskTests(unittest.TestCase):
    def test_big_files_are_read_through_one_at_a_time(self):
        folder = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, folder, ignore_errors=True)
        path = os.path.join(folder, "book.zim")
        with open(path, "wb") as f:
            f.write(b"x" * 2048)
        turn = mock.MagicMock()
        with mock.patch.object(util, "_one_big_read", turn):
            small = util.file_hash(path)
            self.assertEqual(turn.__enter__.call_count, 0, "small files are read side by side")
            with mock.patch.object(util, "BIG_READ", 1024):
                self.assertEqual(util.file_hash(path), small)
        self.assertEqual(turn.__enter__.call_count, 1)


if __name__ == "__main__":
    unittest.main()
