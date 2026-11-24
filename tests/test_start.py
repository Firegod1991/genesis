"""One start on every machine: the book reader unpacked for Windows and for both kinds of Mac, a first
start that offers something to download, the starter itself, and the launchers for Windows."""

import argparse
import io
import os
import shutil
import sys
import tarfile
import tempfile
import types
import unittest
import zipfile
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from genesis import __main__ as command  # noqa: E402
from genesis import box, catalog, fetch  # noqa: E402


class ReaderTests(unittest.TestCase):
    def setUp(self):
        self.drive = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.drive, ignore_errors=True)
        self.vault = os.path.join(self.drive, "vault")
        os.makedirs(os.path.join(self.vault, "tools"))

    def packed(self, machine):
        top = "kiwix-tools_%s-3.8.2" % machine
        with tarfile.open(os.path.join(self.vault, "tools", top + ".tar.gz"), "w:gz") as t:
            for name in ("kiwix-serve", "kiwix-manage"):
                info = tarfile.TarInfo(top + "/" + name)
                info.size, info.mode = 5, 0o755
                t.addfile(info, io.BytesIO(b"serve"))

    def test_the_kiwix_server_for_windows_and_for_both_kinds_of_mac_is_unpacked_too(self):
        with zipfile.ZipFile(os.path.join(self.vault, "tools", "kiwix-tools_win-x86_64-3.8.1.zip"), "w") as z:
            z.writestr("kiwix-serve.exe", b"serve for windows")  # no folder around them, and the libraries beside
            z.writestr("icudt74.dll", b"library")
        self.packed("macos-x86_64")
        self.packed("macos-arm64")
        self.assertEqual(box.unpack_programs(self.vault), ["the Kiwix server for macos-arm64", "the Kiwix server for macos-x64",
                                                           "the Kiwix server for windows-x64"])
        with open(os.path.join(self.vault, "runtime", "windows-x64", "kiwix", "kiwix-serve.exe"), "rb") as f:
            self.assertEqual(f.read(), b"serve for windows")
        self.assertTrue(os.path.isfile(os.path.join(self.vault, "runtime", "windows-x64", "kiwix", "icudt74.dll")))
        for mac in ("macos-x64", "macos-arm64"):
            exe = os.path.join(self.vault, "runtime", mac, "kiwix", "kiwix-serve")
            self.assertTrue(os.path.isfile(exe), mac)
            if os.name != "nt":
                self.assertTrue(os.access(exe, os.X_OK), mac)
        self.assertEqual(box.unpack_programs(self.vault), [], "done once, not on every start")
        with mock.patch.object(box, "log") as said, mock.patch.object(box.shutil, "which", return_value=None):
            box.ready(argparse.Namespace(vault=self.vault))
        self.assertIn("Starts with no Docker on: macos-arm64, macos-x64, windows-x64", said.call_args[0][0])

    def test_a_zip_cannot_write_outside_its_folder(self):
        with zipfile.ZipFile(os.path.join(self.vault, "tools", "kiwix-tools_win-x86_64-9.zip"), "w") as z:
            z.writestr("../../../evil.exe", b"x")
        with self.assertRaises(ValueError):
            box.unpack_programs(self.vault)
        self.assertFalse(os.path.exists(os.path.join(self.drive, "evil.exe")))

    def test_a_rebuilt_release_is_known_and_a_build_for_another_system_library_is_not(self):
        known = {"kiwix-tools_linux-x86_64-3.8.2.tar.gz": "linux-x64", "kiwix-tools_linux-aarch64-3.7.0-2.tar.gz": "linux-arm64",
                 "kiwix-tools_macos-x86_64-3.8.2.tar.gz": "macos-x64", "kiwix-tools_win-x86_64-3.8.1.zip": "windows-x64"}
        for name, machine in known.items():
            self.assertEqual(box.MACHINE[box.KIWIX.match(name).group(1)], machine, name)
        for name in ("kiwix-tools_linux-x86_64-musl-3.8.2.tar.gz", "kiwix-tools_win-i686-3.7.0-2.zip",
                     "kiwix-tools_linux-x86_64-3.8.2.tar.gz.md5", "kiwix-tools_linux-armv6-3.8.2.tar.gz"):
            self.assertIsNone(box.KIWIX.match(name), name)

    def test_every_machine_genesis_starts_on_is_given_a_book_reader(self):
        reader = next(i for i in catalog.load_pack("tools")["items"] if i["id"] == "kiwix_tools")
        machines = {m for m in catalog.PLATFORMS if m != "android"}
        self.assertEqual(machines - set(reader["source"]["files"]), set())
        for machine, pattern in reader["source"]["files"].items():  # and each is one the drive knows how to unpack
            name = pattern.replace("[\\d.]+(?:-\\d+)?", "3.8.2").replace("\\.", ".")
            self.assertEqual(box.MACHINE[box.KIWIX.match(name).group(1)], machine, name)

    def test_a_windows_pc_without_the_runtime_the_reader_needs_is_told_so(self):
        folder = os.path.join(self.vault, "runtime", catalog.host_platform(), "kiwix")
        os.makedirs(folder)
        os.makedirs(os.path.join(self.vault, "zim"))
        for name in ("kiwix-serve", "kiwix-manage"):
            open(os.path.join(folder, name + (".exe" if os.name == "nt" else "")), "w").close()
        open(os.path.join(self.vault, "zim", "wikipedia.zim"), "w").close()
        said, run = [], os.path.join(self.drive, "run")
        os.makedirs(run)
        quiet = {k: v for k, v in os.environ.items() if k not in ("DISPLAY", "WAYLAND_DISPLAY")}
        with mock.patch.object(box, "reader_runs", return_value=False), mock.patch.object(box.subprocess, "call") as called, \
                mock.patch.object(box, "_spawn", return_value=4001), mock.patch.object(box, "_listening", return_value=False), \
                mock.patch.object(box, "_run_dir", return_value=run), mock.patch.object(box, "log", side_effect=said.append), \
                mock.patch.object(box.shutil, "which", return_value=None), mock.patch.object(box.time, "sleep"), \
                mock.patch.dict(os.environ, quiet, clear=True):
            self.assertIsNone(box.list_books(self.vault), "no program is run that Windows would answer with an error box")
            self.assertEqual(called.call_count, 0)
            self.assertEqual([name for name, _, _, _ in box.plan(self.vault)], ["dashboard"])
            box.start(argparse.Namespace(vault=self.vault, no_browser=True))
        told = [line for line in said if line.startswith("not started: the book reader")]
        self.assertEqual(len(told), 1)
        self.assertIn("Visual C++", told[0])
        self.assertIn(box.REDIST, told[0])
        if os.name != "nt":
            self.assertTrue(box.reader_runs(), "only Windows can lack it")

    @unittest.skipIf(os.name == "nt", "Windows has no such limit")
    def test_the_reader_may_hold_many_books_open_on_a_mac_as_well(self):
        import resource
        self.assertGreaterEqual(box.many_files(), resource.getrlimit(resource.RLIMIT_NOFILE)[0])
        set_to = []

        def setrlimit(which, limits):  # a Mac: room for 256 at the start, no ceiling named, and a kernel that allows 10240
            if limits[0] > 10240:
                raise ValueError("current limit exceeds maximum limit")
            set_to.append(limits)

        mac = types.SimpleNamespace(RLIMIT_NOFILE=8, RLIM_INFINITY=2 ** 63 - 1, getrlimit=lambda which: (256, 2 ** 63 - 1),
                                    setrlimit=setrlimit)
        with mock.patch.dict(sys.modules, {"resource": mac}):
            self.assertEqual(box.many_files(), 10240)
        self.assertEqual(set_to, [(10240, 2 ** 63 - 1)])
        mac.getrlimit = lambda which: (1048576, 1048576)  # a system that already allows plenty is left alone
        with mock.patch.dict(sys.modules, {"resource": mac}):
            self.assertEqual(box.many_files(), 1048576)
        self.assertEqual(len(set_to), 1)


class FirstStartTests(unittest.TestCase):
    def setUp(self):
        self.top = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.top, ignore_errors=True)
        self.vault = os.path.join(self.top, "vault")

    def test_a_first_start_offers_a_starter_and_brings_down_what_was_chosen(self):
        for answer, tier in (("", "starter"), ("1", "starter"), ("2", "seed"), ("3", None), ("no", None)):
            asked, args = [], argparse.Namespace(vault=self.vault)
            with mock.patch.object(fetch, "fetch", side_effect=lambda a: asked.append((a.tier, a.yes, a.keep_going)) or 0), \
                    mock.patch.object(box, "ready") as made_ready, mock.patch("builtins.print") as shown:
                self.assertEqual(box.first(args, self.vault, ask=lambda prompt: answer), tier, answer)
            self.assertEqual(asked, [(tier, True, True)] if tier else [], answer)
            self.assertEqual(made_ready.call_count, 1 if tier else 0, answer)
            page = "\n".join(str(call[0][0]) for call in shown.call_args_list)
            self.assertIn(self.vault, page, "it says where the downloads go")
            self.assertRegex(page, r"1  Starter  about \d+(\.\d+)? [MG]B\.")
            self.assertRegex(page, r"2  Seed     about \d+(\.\d+)? GB\.")

    def test_a_vault_with_anything_in_it_is_not_a_first_start(self):
        self.assertTrue(box.nothing_yet(self.vault))
        os.makedirs(os.path.join(self.vault, "zim"))
        self.assertTrue(box.nothing_yet(self.vault), "empty folders are still nothing")
        book = os.path.join(self.vault, "zim", "wikipedia.zim")
        open(book, "w").close()
        self.assertFalse(box.nothing_yet(self.vault))
        os.remove(book)
        os.makedirs(os.path.join(self.vault, "documents"))
        self.assertFalse(box.nothing_yet(self.vault))
        os.rmdir(os.path.join(self.vault, "documents"))
        os.makedirs(os.path.join(self.vault, ".genesis"))
        open(os.path.join(self.vault, ".genesis", "state.json"), "w").close()
        self.assertFalse(box.nothing_yet(self.vault), "a fetch that was cut short has still begun")

    def test_only_a_first_start_with_someone_at_the_keyboard_asks(self):
        run, code = os.path.join(self.top, "run"), os.path.join(self.top, "code")
        os.makedirs(run)
        quiet = {k: v for k, v in os.environ.items() if k not in ("DISPLAY", "WAYLAND_DISPLAY", "GENESIS_VAULT")}

        def start(keyboard, **more):
            with mock.patch.object(box, "first") as first, mock.patch.object(box, "_spawn", return_value=4001), \
                    mock.patch.object(box, "_listening", return_value=True), mock.patch.object(box, "_run_dir", return_value=run), \
                    mock.patch.object(box, "log"), mock.patch.object(box.shutil, "which", return_value=None), \
                    mock.patch.object(fetch, "ROOT", code), \
                    mock.patch.object(box.sys, "stdin", mock.Mock(isatty=lambda: keyboard)), mock.patch.dict(os.environ, quiet, clear=True):
                box.start(argparse.Namespace(no_browser=True, **more))
            return first.call_count

        # A fresh copy of the code, with no vault named: the vault is the folder inside it.
        self.assertEqual(start(True, vault=None), 1)
        self.assertTrue(os.path.isdir(os.path.join(code, "vault", ".genesis")), "empty shelves still have a vault to start from")
        shutil.rmtree(code)
        self.assertEqual(start(True, vault=None, no_ask=True), 0, "asked not to")
        shutil.rmtree(code)
        self.assertEqual(start(False, vault=None), 0, "nobody to ask, as from a desktop icon")
        self.assertTrue(os.path.isdir(os.path.join(code, "vault", ".genesis")))
        # A vault named by hand: an empty folder is a first start, a missing one is a drive that is not plugged in.
        named = os.path.join(self.top, "named")
        with self.assertRaises(SystemExit):
            start(True, vault=named)
        self.assertFalse(os.path.exists(named), "nothing is made where a drive should have been")
        os.makedirs(named)
        self.assertEqual(start(True, vault=named), 1)
        os.makedirs(os.path.join(named, "zim"))
        open(os.path.join(named, "zim", "wikipedia.zim"), "w").close()
        self.assertEqual(start(True, vault=named), 0, "a vault with a book in it is not a first start")

    def test_on_a_drive_the_vault_beside_the_code_is_found_on_every_system(self):
        code, inside, beside = os.path.join(self.top, "genesis"), os.path.join(self.top, "genesis", "vault"), self.vault
        os.makedirs(code)
        clean = {k: v for k, v in os.environ.items() if k != "GENESIS_VAULT"}
        with mock.patch.object(command, "ROOT", code), mock.patch.dict(os.environ, clean, clear=True):
            self.assertIsNone(command.vault_beside(), "a fresh copy keeps its vault inside itself")
            os.makedirs(beside)
            self.assertIsNone(command.vault_beside(), "a folder that only happens to be called vault is not one")
            os.makedirs(os.path.join(beside, ".genesis"))
            self.assertEqual(command.vault_beside(), beside)
            self.assertEqual(fetch.vault_path(argparse.Namespace(vault=None)), beside)
            self.assertEqual(fetch.vault_path(argparse.Namespace(vault=os.path.join(self.top, "typed"))),
                             os.path.join(self.top, "typed"), "a vault named by hand still wins")
        with mock.patch.object(command, "ROOT", code), mock.patch.dict(os.environ, clean, clear=True):
            os.makedirs(inside)
            self.assertIsNone(command.vault_beside(), "its own vault comes first")
        with mock.patch.object(command, "ROOT", code), mock.patch.dict(os.environ, dict(clean, GENESIS_VAULT="said"), clear=True):
            shutil.rmtree(inside)
            self.assertIsNone(command.vault_beside(), "a vault named in the environment is left alone")


class StarterTests(unittest.TestCase):
    def test_the_starter_is_a_part_of_seed_small_enough_to_try(self):
        locked = {iid: entry for _, iid, entry in catalog.all_locked()}
        named = set(catalog.STARTER_ITEMS)
        self.assertEqual(named - set(locked), set(), "the starter names items the catalog does not have")
        self.assertEqual({iid for iid in named if locked[iid].get("tier") != "seed"}, set(), "every item of the starter is in seed")
        for machine in (m for m in catalog.PLATFORMS if m != "android"):
            starter = catalog.select(tier=catalog.STARTER, platforms=[machine, "android"])
            ids = {iid for _, iid, _, _ in starter}
            seed = {iid for _, iid, _, _ in catalog.select(tier="seed", platforms=[machine, "android"])}
            self.assertLessEqual(ids, seed, machine)
            self.assertLessEqual(named, ids, machine)
            manuals = {iid for iid in seed if (locked[iid].get("dest") or "").startswith("documents")}
            self.assertGreater(len(manuals), 30)
            self.assertLessEqual(manuals, ids, "every manual of seed comes with the starter")
            self.assertLess(sum(catalog.entry_size(e, f) or 0 for _, _, e, f in starter), 2e9, "it is meant to come down in minutes")

    def test_a_tier_can_be_named_on_the_command_line(self):
        for tier in ["starter"] + catalog.TIERS:
            self.assertEqual(command.build_parser().parse_args(["fetch", "--tier", tier]).tier, tier)
        self.assertTrue(command.build_parser().parse_args(["start", "--no-ask"]).no_ask)


class LauncherTests(unittest.TestCase):
    def test_the_windows_launchers_run_whatever_line_ending_they_arrive_with(self):
        # A copy from git has Windows line endings and a copy from a ZIP may not. The command processor loses
        # its place at a label in a file with the wrong ones, so these files use none, and stay plain ASCII.
        for name in ("genesis.cmd", "Start Genesis.cmd", "Stop Genesis.cmd"):
            with open(os.path.join(ROOT, name), "rb") as f:
                text = f.read().decode("ascii").replace("\r\n", "\n")
            self.assertTrue(text.startswith("@echo off\n"), name)
            self.assertNotRegex(text, r"(?im)^\s*:[a-z]|\bgoto\b|\bcall\s+:", name)
        with open(os.path.join(ROOT, ".gitattributes"), encoding="utf-8") as f:
            self.assertIn("*.cmd text eol=crlf", f.read())


if __name__ == "__main__":
    unittest.main()
