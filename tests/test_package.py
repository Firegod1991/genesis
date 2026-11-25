"""The downloads: one folder each, the right launchers for each system, Python inside the one for Windows."""

import os
import shutil
import sys
import tarfile
import tempfile
import unittest
import zipfile
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))

import package  # noqa: E402


def fake_python(cache):
    os.makedirs(cache, exist_ok=True)
    path = os.path.join(cache, "python-embed.zip")
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("python.exe", b"MZ")
        z.writestr("python314._pth", b"python314.zip\r\n.\r\n\r\n# Uncomment to run site.main() automatically\r\n#import site\r\n")
    return path


@unittest.skipUnless(shutil.which("git") and os.path.isdir(os.path.join(ROOT, ".git")), "needs the git checkout")
class PackageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.out = tempfile.mkdtemp()
        with mock.patch.object(package, "python_for_windows", side_effect=fake_python), \
                mock.patch("builtins.print"):
            package.main(["--out", cls.out])
        cls.win = zipfile.ZipFile(os.path.join(cls.out, "Genesis-Windows.zip"))
        cls.mac = zipfile.ZipFile(os.path.join(cls.out, "Genesis-Mac.zip"))
        cls.linux = tarfile.open(os.path.join(cls.out, "Genesis-Linux.tar.gz"))

    @classmethod
    def tearDownClass(cls):
        cls.win.close(), cls.mac.close(), cls.linux.close()
        shutil.rmtree(cls.out, ignore_errors=True)

    def test_one_folder_each_with_how_to_start(self):
        for names in (self.win.namelist(), self.mac.namelist(), self.linux.getnames()):
            self.assertTrue(all(n.startswith("Genesis/") for n in names))
            self.assertIn("Genesis/HOW TO START.txt", names)
            self.assertIn("Genesis/genesis/box.py", names)
            self.assertFalse([n for n in names if n.startswith(("Genesis/tests/", "Genesis/.github/"))])

    def test_each_system_gets_its_own_launchers(self):
        win, mac, linux = set(self.win.namelist()), set(self.mac.namelist()), set(self.linux.getnames())
        self.assertIn("Genesis/Start Genesis.cmd", win)
        self.assertFalse({"Genesis/Start Genesis.command", "Genesis/Start Genesis.desktop"} & win)
        self.assertIn("Genesis/Start Genesis.command", mac)
        self.assertFalse({"Genesis/Start Genesis.cmd", "Genesis/Start Genesis.desktop"} & mac)
        self.assertIn("Genesis/Start Genesis.desktop", linux)
        self.assertFalse({"Genesis/Start Genesis.cmd", "Genesis/Start Genesis.command"} & linux)
        self.assertEqual(self.win.read("Genesis/Start Genesis.cmd").count(b"\n"), self.win.read("Genesis/Start Genesis.cmd").count(b"\r\n"))

    def test_python_rides_along_for_windows_and_finds_genesis(self):
        self.assertEqual(self.win.read("Genesis/python/python.exe"), b"MZ")
        self.assertIn(b"\r\n.\r\n..\r\n", self.win.read("Genesis/python/python314._pth"))

    def test_what_runs_is_marked_executable(self):
        mode = lambda info: (info.external_attr >> 16) & 0o777  # noqa: E731
        self.assertEqual(mode(self.mac.getinfo("Genesis/Start Genesis.command")), 0o755)
        self.assertEqual(mode(self.mac.getinfo("Genesis/bin/genesis")), 0o755)
        self.assertEqual(mode(self.mac.getinfo("Genesis/README.md")), 0o644)
        self.assertEqual(self.linux.getmember("Genesis/Start Genesis.desktop").mode, 0o755)
        self.assertEqual(self.linux.getmember("Genesis/bin/genesis-desktop").mode, 0o755)

    def test_the_sums_name_each_download(self):
        with open(os.path.join(self.out, "SHA256SUMS")) as f:
            self.assertEqual(sorted(line.split()[1] for line in f),
                             ["Genesis-Linux.tar.gz", "Genesis-Mac.zip", "Genesis-Windows.zip"])


if __name__ == "__main__":
    unittest.main()
