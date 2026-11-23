"""The safe: a family's own papers, one encrypted archive on the drive, an ordinary folder while it is open."""

import argparse
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

from genesis import box, catalog, safe, seal  # noqa: E402
from genesis.util import file_hash, read_json  # noqa: E402

STUB = [sys.executable, os.path.join(ROOT, "tests", "stub_7z.py")]
REAL = next((p for p in (shutil.which("7zz"), shutil.which("7z")) if p and safe.takes_stdin(p)), None)
GOOD, OTHER = "four plain words here", "another phrase entirely"
WIDE = "pässwörd 日本語"
BANNERS = {
    "new": "\n7-Zip (z) 26.06 (arm64) : Copyright (c) 1999-2026 Igor Pavlov : 2026-11-06\n 64-bit arm_v:8-A locale=C.UTF-8\n",
    "windows": "\r\n7-Zip 24.08 (x64) : Copyright (c) 1999-2024 Igor Pavlov : 2024-08-11\r\n",
    "p7zip": "\n7-Zip [64] 16.02 : Copyright (c) 1999-2016 Igor Pavlov : 2016-05-21\n"
             "p7zip Version 16.02 (locale=en_US.UTF-8,Utf16=on,HugeFiles=on,64 bits,4 CPUs x64)\n",
    "ancient": "\n7-Zip 9.20  Copyright (c) 1999-2010 Igor Pavlov  2010-11-18\n",
    "other": "this is some other program\n",
}


class SafeTests(unittest.TestCase):
    program = STUB

    def setUp(self):
        self.drive = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.drive, ignore_errors=True)
        self.vault = os.path.join(self.drive, "vault")
        os.makedirs(self.vault)
        self.open = os.path.join(self.drive, "open", "safe")
        self.said, self.typed, self.asked = [], [], []

        def ask(prompt):
            self.asked.append(prompt)
            return self.typed.pop(0)
        for patch in (mock.patch.dict(os.environ, {"GENESIS_SAFE_OPEN": self.open}),
                      mock.patch.object(safe, "seven_zip", return_value=self.program),
                      mock.patch.object(safe, "_ask", side_effect=ask),
                      mock.patch.object(safe, "_show"),
                      mock.patch.object(safe, "log", side_effect=self.said.append)):
            patch.start()
            self.addCleanup(patch.stop)

    def put(self, rel, text):
        path = os.path.join(self.open, *rel.split("/"))
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)

    def papers(self):
        out = {}
        for here, _, names in os.walk(self.open):
            for name in names:
                with open(os.path.join(here, name), encoding="utf-8") as f:
                    out[os.path.relpath(os.path.join(here, name), self.open).replace(os.sep, "/")] = f.read()
        return out

    def lock(self, *typed, **how):
        self.typed = list(typed)
        return safe.lock_safe(self.vault, **how)

    def unlock(self, *typed):
        self.typed = list(typed)
        return safe.open_safe(self.vault)

    def filled(self):
        """A locked safe with two papers in it."""
        self.assertEqual(self.unlock(), 0)
        self.put("deed of the house.txt", "ours")
        self.put("medical/blood types.txt", "A+ and O-")
        self.assertEqual(self.lock(GOOD, GOOD), 0)

    def test_a_new_safe_is_filled_locked_and_opens_again_with_its_passphrase(self):
        self.assertEqual(safe.status(self.vault)["exists"], False)
        self.filled()
        self.assertFalse(os.path.exists(self.open), "locked means the open folder is gone")
        now = safe.status(self.vault)
        self.assertEqual((now["exists"], now["open"]), (True, False))
        self.assertTrue(os.path.isfile(os.path.join(self.vault, "files", "safe", "READ ME.txt")))
        self.assertEqual(self.unlock(GOOD), 0)
        self.assertEqual(self.papers(), {"deed of the house.txt": "ours", "medical/blood types.txt": "A+ and O-"})
        self.assertTrue(safe.status(self.vault)["open"])

    def test_a_wrong_passphrase_opens_nothing(self):
        self.filled()
        self.assertEqual(self.unlock("wrong", "wrong again", "still wrong"), 1)
        self.assertFalse(os.path.exists(self.open))
        self.assertIn("The safe stays locked. Nothing was changed.", self.said)

    def test_locking_asks_for_the_passphrase_the_safe_already_has(self):
        self.filled()
        self.unlock(GOOD)
        self.put("new.txt", "added")
        before = file_hash(safe.archive(self.vault))
        self.assertEqual(self.lock(OTHER, OTHER, OTHER), 1, "a slip of the fingers must not lock the family out")
        self.assertEqual(file_hash(safe.archive(self.vault)), before)
        self.assertEqual(self.papers()["new.txt"], "added", "and nothing that was open is lost")
        self.assertEqual(self.lock(GOOD), 0)
        self.unlock(GOOD)
        self.assertEqual(self.papers()["new.txt"], "added")

    def test_the_safe_as_it_was_before_the_last_lock_is_kept(self):
        self.filled()
        self.unlock(GOOD)
        self.put("deed of the house.txt", "sold")
        self.assertEqual(self.lock(GOOD), 0)
        folder = os.path.join(self.vault, "files", "safe")
        self.assertEqual(sorted(os.listdir(folder)), ["READ ME.txt", "safe.7z", "safe.7z.bak"])
        os.replace(os.path.join(folder, "safe.7z.bak"), os.path.join(folder, "safe.7z"))
        self.unlock(GOOD)
        self.assertEqual(self.papers()["deed of the house.txt"], "ours")

    def test_an_emptied_folder_never_replaces_the_papers(self):
        self.filled()
        self.unlock(GOOD)
        shutil.rmtree(self.open)
        os.makedirs(self.open)
        before = file_hash(safe.archive(self.vault))
        self.assertEqual(self.lock(), 1)
        self.assertEqual(self.asked[-1:], ["Passphrase for the safe: "], "not even a passphrase is asked for")
        self.assertTrue(os.path.isdir(self.open))
        self.assertEqual(self.lock(empty=True), 0)
        self.assertFalse(os.path.exists(self.open))
        self.assertEqual(file_hash(safe.archive(self.vault)), before)

    def test_a_new_passphrase_needs_the_old_one_first(self):
        self.filled()
        self.unlock(GOOD)
        self.assertEqual(self.lock(GOOD, OTHER, OTHER, renew=True), 0)
        self.assertEqual(self.unlock(GOOD, GOOD, GOOD), 1)
        self.assertEqual(self.unlock(OTHER), 0)
        self.assertEqual(self.papers()["deed of the house.txt"], "ours")

    def test_a_short_or_mistyped_passphrase_is_asked_for_again(self):
        self.unlock()
        self.put("a.txt", "a")
        self.assertEqual(self.lock("short", GOOD, "mistyped it here", GOOD, GOOD), 0)
        self.assertIn("That is too short.", self.said)
        self.assertIn("Those two did not match.", self.said)
        self.assertEqual(self.unlock(GOOD), 0)

    def test_giving_up_on_a_passphrase_leaves_the_papers_where_they_are(self):
        self.unlock()
        self.put("a.txt", "a")
        self.assertEqual(self.lock("x", "y", "z"), 1)
        self.assertEqual(self.papers(), {"a.txt": "a"})
        self.assertFalse(os.path.exists(safe.archive(self.vault)))

    def test_nothing_put_in_means_no_safe_is_made(self):
        self.unlock()
        self.assertEqual(self.lock(), 0)
        self.assertFalse(os.path.exists(self.open))
        self.assertFalse(os.path.exists(safe.archive(self.vault)))

    def test_the_icon_opens_a_shut_safe_and_locks_an_open_one(self):
        args = argparse.Namespace(vault=self.vault, action=None)
        self.typed = []
        self.assertEqual(safe.main(args), 0)
        self.put("a.txt", "a")
        self.typed = [GOOD, GOOD]
        self.assertEqual(safe.main(args), 0, "no terminal to ask on: an open safe is locked")
        self.assertFalse(os.path.exists(self.open))
        self.assertTrue(os.path.isfile(safe.archive(self.vault)))

    def test_the_passphrase_never_rides_on_a_command_line(self):
        seen = []
        real = safe.subprocess.run

        def run(cmd, **kw):
            seen.append((cmd, kw.get("input")))
            return real(cmd, **kw)
        with mock.patch.object(safe.subprocess, "run", side_effect=run):
            self.filled()
            self.unlock(GOOD)
        self.assertTrue(seen)
        for cmd, typed in seen:
            self.assertNotIn(GOOD, " ".join(cmd))
            self.assertEqual(typed, (GOOD + "\n").encode())
            self.assertIn("-sccUTF-8", cmd, "the passphrase is handed over as UTF 8 on every system")
        first = len(self.program) if isinstance(self.program, list) else 1
        opening = [cmd for cmd, _ in seen if cmd[first] in ("t", "x")]
        self.assertTrue(opening and all("-p" not in cmd for cmd in opening),
                        "a bare -p makes 7-Zip try an empty passphrase when it opens an archive")


@unittest.skipUnless(REAL, "needs 7-Zip on this machine")
class RealSevenZipTests(SafeTests):
    """The same promises, kept by the real program."""
    program = REAL

    def test_the_archive_gives_away_nothing_not_even_the_names(self):
        self.filled()
        with open(safe.archive(self.vault), "rb") as f:
            raw = f.read()
        self.assertTrue(raw.startswith(b"7z\xbc\xaf\x27\x1c"), "an ordinary .7z file any computer with 7-Zip opens")
        for word in (b"deed", b"blood", b"ours", b"medical"):
            self.assertNotIn(word, raw)

    def test_a_passphrase_beyond_ascii_is_the_same_passphrase_on_every_machine(self):
        self.unlock()
        self.put("a.txt", "a")
        self.assertEqual(self.lock(WIDE, WIDE), 0)
        # Handed over as an argument, which no console can bend. Only a test may do that.
        plain = subprocess.run([REAL, "t", "-bso0", "-bsp0", "-p" + WIDE, safe.archive(self.vault)],
                               stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        self.assertEqual(plain.returncode, 0, plain.stdout.decode("utf-8", "replace"))
        self.assertEqual(self.unlock(WIDE), 0)
        self.assertEqual(self.papers(), {"a.txt": "a"})


class WhichSevenZipTests(unittest.TestCase):
    """Only a 7-Zip that can be handed a passphrase is used."""

    def setUp(self):
        self.vault = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.vault, ignore_errors=True)
        self.on_path = {}

        def run(cmd, **kw):
            if cmd[0] not in BANNERS:
                raise OSError("cannot run %s" % cmd[0])
            return mock.Mock(stdout=BANNERS[cmd[0]].encode())
        for patch in (mock.patch.dict(safe._known, clear=True),
                      mock.patch.dict(os.environ, {"ProgramFiles": "", "ProgramFiles(x86)": ""}),
                      mock.patch.object(safe.subprocess, "run", side_effect=run),
                      mock.patch.object(safe.shutil, "which", side_effect=lambda name: self.on_path.get(name))):
            patch.start()
            self.addCleanup(patch.stop)

    def test_a_current_7zip_is_used_and_an_old_p7zip_is_not(self):
        self.assertTrue(safe.takes_stdin("new"))
        self.assertTrue(safe.takes_stdin("windows"))
        self.assertFalse(safe.takes_stdin("p7zip"), "it asks the terminal itself and would seem to hang")
        self.assertFalse(safe.takes_stdin("ancient"))
        self.assertFalse(safe.takes_stdin("other"))
        self.assertFalse(safe.takes_stdin("missing"))

    def test_the_old_one_is_passed_over_for_a_current_one(self):
        self.on_path = {"7z": "p7zip", "7za": "new"}
        self.assertEqual(safe.seven_zip(self.vault), "new")

    def test_with_only_the_old_one_it_says_so_and_what_to_do(self):
        self.on_path = {"7z": "p7zip"}
        said = []
        with mock.patch.object(safe, "log", side_effect=said.append):
            self.assertIsNone(safe.seven_zip(self.vault))
            self.assertEqual(safe.status(self.vault)["program"], False)
            self.assertEqual(safe.open_safe(self.vault), 1)
        self.assertIn("(p7zip) is the old p7zip", said[0])
        self.assertIn("genesis fetch --id sevenzip", said[0])


class SafeOnTheDriveTests(unittest.TestCase):
    def setUp(self):
        self.drive = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.drive, ignore_errors=True)
        self.vault = os.path.join(self.drive, "vault")
        os.makedirs(os.path.join(self.vault, "tools"))

    def test_the_7zip_on_the_drive_is_unpacked_and_found(self):
        packed = os.path.join(self.vault, "tools", "7z2606-linux-arm64.tar.xz")
        with tarfile.open(packed, "w:xz") as t:
            for name in ("7zz", "7zzs", "License.txt", "MANUAL/start.htm"):
                path = os.path.join(self.drive, "member")
                with open(path, "w") as f:
                    f.write(name)
                info = t.gettarinfo(path, name)
                info.mode = 0o755 if name.startswith("7zz") else 0o644
                with open(path, "rb") as f:
                    t.addfile(info, f)
        self.assertEqual(box.unpack_programs(self.vault), ["7-Zip for linux-arm64"])
        into = os.path.join(self.vault, "runtime", "linux-arm64", "7zip")
        self.assertEqual(sorted(n for n in os.listdir(into) if not n.startswith(".")), ["7zz", "7zzs", "License.txt"])
        if os.name != "nt":
            self.assertTrue(os.access(os.path.join(into, "7zzs"), os.X_OK))
        self.assertEqual(box.unpack_programs(self.vault), [], "done once")
        with mock.patch.object(catalog, "host_platform", return_value="linux-arm64"):
            self.assertEqual(safe.seven_zip(self.vault), os.path.join(into, "7zzs"), "the build that needs nothing else first")
        with mock.patch.object(catalog, "host_platform", return_value="linux-x64"), \
                mock.patch.object(safe.shutil, "which", return_value=None), \
                mock.patch.dict(os.environ, {"ProgramFiles": "", "ProgramFiles(x86)": ""}):
            self.assertIsNone(safe.seven_zip(self.vault), "a build for another machine is no use here")

    def test_without_7zip_it_says_where_to_get_it(self):
        said = []
        with mock.patch.object(safe, "seven_zip", return_value=None), mock.patch.object(safe, "log", side_effect=said.append):
            self.assertEqual(safe.open_safe(self.vault), 1)
        self.assertIn("genesis fetch --id sevenzip", said[0])

    def test_the_drive_gets_an_icon_for_the_safe(self):
        os.makedirs(os.path.join(self.drive, "genesis"))
        with mock.patch.object(box, "ROOT", os.path.join(self.drive, "genesis")):
            box.dress(self.vault)
        with open(os.path.join(self.drive, "Genesis Safe.desktop"), encoding="utf-8") as f:
            entry = f.read()
        self.assertIn("Terminal=true", entry, "the passphrase is typed in a terminal window")
        self.assertIn("lock.svg", entry)
        with open(os.path.join(self.drive, "Genesis Safe.sh"), encoding="utf-8") as f:
            self.assertIn('genesis/bin/genesis" safe', f.read())
        with open(os.path.join(self.drive, "READ ME FIRST.txt"), encoding="utf-8") as f:
            guide = f.read()
        self.assertIn('sh "%s/Start Genesis.sh"' % self.drive, guide, "the lines to type carry this machine's path to the drive")
        self.assertIn("will offer to format it. Always answer no.", guide)
        self.assertNotIn("%(", guide)
        self.assertTrue(os.path.isfile(os.path.join(ROOT, "dashboard", "static", "icons", "lock.svg")))

    def test_what_people_keep_on_the_drive_is_left_out_of_the_seal(self):
        for rel in ("zim/book.zim", "files/safe/safe.7z", "files/notes/list.md"):
            path = os.path.join(self.vault, *rel.split("/"))
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w") as f:
                f.write(rel)
        with mock.patch.object(seal, "log"), mock.patch.object(seal.shutil, "which", return_value=None):
            seal.seal(argparse.Namespace(vault=self.vault))
        sealed = read_json(os.path.join(self.vault, "restore", "MANIFEST.json"))["files"]
        self.assertIn("zim/book.zim", sealed)
        self.assertFalse([k for k in sealed if k.startswith("files/")], "a safe that is used would look like a damaged drive")


if __name__ == "__main__":
    unittest.main()
