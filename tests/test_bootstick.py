"""A way back in: the stick that starts a PC and the card that starts a Pi are made only from disks that may be erased."""

import argparse
import io
import lzma
import os
import shutil
import sys
import tarfile
import tempfile
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from genesis import bootstick  # noqa: E402

GB = 1000 ** 3


class Machine(unittest.TestCase):
    """A made up machine: an inside disk, the card it runs from, the Genesis drive, a stick, and an empty reader."""

    def setUp(self):
        self.top = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.top, ignore_errors=True)
        self.sys, self.vault = os.path.join(self.top, "sys", "block"), os.path.join(self.top, "media", "GENESIS", "vault")
        for sub in ("tools", "isos"):
            os.makedirs(os.path.join(self.vault, sub))
        self.mounts = os.path.join(self.top, "mounts")
        self.disk("nvme0n1", 512 * GB, bus="pci0000:00", parts=["nvme0n1p1"])
        self.disk("mmcblk0", 64 * GB, bus="platform/mmc", parts=["mmcblk0p1", "mmcblk0p2"])
        self.disk("sda", 2000 * GB, bus="usb1/1-1", model="WD My Passport", parts=["sda1"], number="8:0")
        self.disk("sdb", 32 * GB, bus="usb1/1-2", model="SanDisk Ultra", removable=True, parts=["sdb1"])
        self.disk("sdc", 0, bus="usb1/1-3", model="Generic SD/MMC", removable=True)
        self.disk("loop0", 1 * GB, bus="virtual")
        with open(self.mounts, "w") as f:
            f.write("/dev/mmcblk0p2 / ext4 rw 0 0\n/dev/mmcblk0p1 /boot/firmware vfat rw 0 0\n"
                    "/dev/sda1 %s ext4 rw 0 0\n/dev/sdb1 /media/pi/OLD\\040STICK vfat rw 0 0\n"
                    % os.path.dirname(self.vault).replace(" ", "\\040"))
        numbers = {self.vault: "8:1", ROOT: "8:1", "/": "179:2"}
        self.said = []
        for patch in (mock.patch.object(bootstick, "SYS", self.sys), mock.patch.object(bootstick, "MOUNTS", self.mounts),
                      mock.patch.object(bootstick, "_number", side_effect=numbers.get),
                      mock.patch.object(bootstick, "log", side_effect=self.said.append),
                      mock.patch.object(bootstick.sys, "platform", "linux")):
            patch.start()
            self.addCleanup(patch.stop)
        self.keep = [self.vault, ROOT, "/"]

    def disk(self, name, size, bus, model="", removable=False, parts=(), number=None):
        real = os.path.join(self.top, "devices", bus, name)
        os.makedirs(os.path.join(real, "device"))
        os.makedirs(self.sys, exist_ok=True)
        if hasattr(os, "symlink") and os.name != "nt":
            os.symlink(real, os.path.join(self.sys, name))
        else:  # Windows: no links without leave, so the bus is not in the path and only `removable` tells
            shutil.copytree(real, os.path.join(self.sys, name))
        base = os.path.join(self.sys, name)
        for key, value in (("size", str(size // 512)), ("removable", "1" if removable else "0"),
                           ("dev", number or "%d:0" % (len(os.listdir(self.sys)) + 200)), ("device/model", model)):
            with open(os.path.join(base, *key.split("/")), "w") as f:
                f.write(value + "\n")
        for n, part in enumerate(parts, 1):
            os.makedirs(os.path.join(base, part))
            for key, value in (("partition", str(n)), ("dev", "%s:%d" % ((number or "250:0").split(":")[0], n))):
                with open(os.path.join(base, part, key), "w") as f:
                    f.write(value + "\n")

    def by_name(self):
        return {d["name"]: d for d in bootstick.disks()}

    def iso(self, name, size):
        with open(os.path.join(self.vault, "isos", name), "wb") as f:
            f.write(b"\0" * size)

    def args(self, **kw):
        return argparse.Namespace(**dict({"vault": self.vault, "device": None, "pi": False, "lite": False, "iso": None,
                                          "yes": True, "force": False}, **kw))


@unittest.skipIf(os.name == "nt", "disks are found the way Linux lists them")
class GuardTests(Machine):
    def test_every_real_disk_is_seen_and_empty_readers_are_not(self):
        found = self.by_name()
        self.assertEqual(sorted(found), ["mmcblk0", "nvme0n1", "sda", "sdb"])
        self.assertEqual(found["sdb"]["mounted"], ["/media/pi/OLD STICK"], "a space in a name is read as a space")
        self.assertEqual(found["sdb"]["model"], "SanDisk Ultra")
        self.assertEqual(found["sdb"]["bytes"], 32 * GB)

    def test_only_a_stick_or_a_spare_card_may_be_erased(self):
        found = self.by_name()
        self.assertIsNone(bootstick.refusal(found["sdb"], self.keep))
        self.assertIn("holds", bootstick.refusal(found["sda"], self.keep), "the Genesis drive itself, never")
        self.assertIn("holds", bootstick.refusal(found["sda"], self.keep, force=True), "not even when forced")
        self.assertIn("in use by this system", bootstick.refusal(found["mmcblk0"], self.keep, force=True))
        self.assertIn("not a USB stick or a card", bootstick.refusal(found["nvme0n1"], self.keep))
        self.assertIsNone(bootstick.refusal(found["nvme0n1"], self.keep, force=True), "the owner may still insist")

    def test_a_drive_that_is_only_mounted_is_still_protected(self):
        found = self.by_name()
        with mock.patch.object(bootstick, "_number", return_value=None):  # a system that will not say which disk a path is on
            self.assertIn("in use by this system", bootstick.refusal(found["sda"], self.keep, force=True))

    def test_a_big_disk_is_not_taken_for_a_stick(self):
        big = dict(self.by_name()["sdb"], bytes=1000 * GB)
        self.assertIn("big for a stick", bootstick.refusal(big, self.keep))
        self.assertIsNone(bootstick.refusal(big, self.keep, force=True))

    def test_with_no_disk_named_it_lists_what_may_be_used_and_writes_nothing(self):
        with mock.patch.object(bootstick, "ventoy") as made, mock.patch.object(bootstick, "pi_card") as written:
            self.assertEqual(bootstick.main(self.args()), 0)
        made.assert_not_called()
        written.assert_not_called()
        listed = "\n".join(self.said)
        self.assertIn("/dev/sdb", listed)
        self.assertNotIn("/dev/sda", listed)
        self.assertNotIn("/dev/mmcblk0", listed)
        self.assertRegex(listed, r'sudo sh ".*genesis" bootstick --device /dev/sdb')

    def test_a_part_of_a_disk_or_a_protected_disk_is_turned_down_before_anything_runs(self):
        with mock.patch.object(bootstick, "ventoy") as made, mock.patch.object(bootstick.os.path, "realpath", side_effect=lambda p: p):
            self.assertEqual(bootstick.main(self.args(device="/dev/sdb1")), 1)
            self.assertIn("Name the whole disk: --device /dev/sdb", self.said[-1])
            self.assertEqual(bootstick.main(self.args(device="/dev/sda", force=True)), 1)
            self.assertIn("Not touching it", self.said[-1])
            self.assertEqual(bootstick.main(self.args(device="/dev/sdz")), 1)
        made.assert_not_called()

    @unittest.skipUnless(hasattr(os, "geteuid"), "needs a system with root")
    def test_it_needs_root_and_says_so(self):
        with mock.patch.object(bootstick, "ventoy") as made, mock.patch.object(bootstick.os, "geteuid", return_value=1000), \
                mock.patch.object(bootstick.os.path, "realpath", side_effect=lambda p: p):
            self.assertEqual(bootstick.main(self.args(device="/dev/sdb")), 1)
            self.assertIn("sudo", self.said[-1])
            made.assert_not_called()
        with mock.patch.object(bootstick, "ventoy", return_value=0) as made, mock.patch.object(bootstick.os, "geteuid", return_value=0), \
                mock.patch.object(bootstick.os.path, "realpath", side_effect=lambda p: p):
            self.assertEqual(bootstick.main(self.args(device="/dev/sdb")), 0)
            self.assertEqual(made.call_args[0][1]["name"], "sdb")

    def test_other_systems_are_pointed_at_their_own_tools(self):
        with mock.patch.object(bootstick.sys, "platform", "win32"):
            self.assertEqual(bootstick.main(self.args(device="/dev/sdb")), 1)
        self.assertIn("Rufus", self.said[-1])


@unittest.skipIf(os.name == "nt", "disks are found the way Linux lists them")
class StickTests(Machine):
    def test_the_systems_are_chosen_most_useful_first_for_the_room_there_is(self):
        for name, size in (("ubuntu-26.04.1-desktop-amd64.iso", 6000), ("debian-live-13.7.0-amd64-xfce.iso", 3000),
                           ("clonezilla-live-3.3.3-37-amd64.iso", 500), ("gparted-live-1.8.1-3-amd64.iso", 600),
                           ("2026-10-06-raspios-trixie-arm64.img.xz", 1400), ("ubuntu-26.04.1-live-server-arm64.iso", 3000)):
            self.iso(name, size)
        folder = os.path.join(self.vault, "isos")
        with mock.patch.object(bootstick, "SPARE", 100):
            self.assertEqual(bootstick.choose(folder, 20000), ["debian-live-13.7.0-amd64-xfce.iso", "clonezilla-live-3.3.3-37-amd64.iso",
                                                               "gparted-live-1.8.1-3-amd64.iso", "ubuntu-26.04.1-desktop-amd64.iso"],
                             "systems for a PC only: nothing built for a Pi goes on a PC stick")
            self.assertEqual(bootstick.choose(folder, 4300), ["debian-live-13.7.0-amd64-xfce.iso", "clonezilla-live-3.3.3-37-amd64.iso",
                                                              "gparted-live-1.8.1-3-amd64.iso"], "what does not fit is left off")
            self.assertEqual(bootstick.choose(folder, 20000, ["Ubuntu", "clone"]),
                             ["ubuntu-26.04.1-desktop-amd64.iso", "ubuntu-26.04.1-live-server-arm64.iso", "clonezilla-live-3.3.3-37-amd64.iso"],
                             "named by the owner: those and only those")
            self.assertEqual(bootstick.choose(folder, 50), [])

    @unittest.skipIf(os.name == "nt", "the installer is a shell script")
    def test_ventoy_is_run_on_the_stick_and_the_systems_are_copied_beside_it(self):
        self.iso("debian-live-13.7.0-amd64-xfce.iso", 3000)
        self.iso("clonezilla-live-3.3.3-37-amd64.iso", 500)
        ran = os.path.join(self.top, "ran")
        script = ('#!/bin/sh\nread a; read b\necho "$@ $a $b" > "%s"\n' % ran).encode()
        with tarfile.open(os.path.join(self.vault, "tools", "ventoy-1.1.18-linux.tar.gz"), "w:gz") as t:
            info = tarfile.TarInfo("ventoy-1.1.18/Ventoy2Disk.sh")
            info.size, info.mode = len(script), 0o755
            t.addfile(info, io.BytesIO(script))
        calls, on_stick = [], []

        def run(cmd, **kw):
            calls.append(cmd[0])
            if cmd[0] == "umount" and os.path.isdir(cmd[1]):
                on_stick.extend(sorted(os.listdir(cmd[1])))
            return 0
        with mock.patch.object(bootstick, "_run", side_effect=run), \
                mock.patch.object(bootstick, "_first_partition", return_value="/dev/sdb1"), mock.patch.object(bootstick, "SPARE", 100):
            self.assertEqual(bootstick.ventoy(self.vault, self.by_name()["sdb"], yes=True), 0)
        with open(ran) as f:
            self.assertEqual(f.read().strip(), "-I /dev/sdb y y", "the whole disk, and its two questions answered only because --yes was given")
        self.assertEqual(calls[0], "umount", "the old contents are unmounted before anything is written")
        self.assertIn("mount", calls)
        self.assertEqual(on_stick, ["READ ME.txt", "clonezilla-live-3.3.3-37-amd64.iso", "debian-live-13.7.0-amd64-xfce.iso"])
        self.assertIn("The stick is ready: 2 systems", self.said[-1])

    def test_without_ventoy_or_without_a_system_nothing_is_touched(self):
        with mock.patch.object(bootstick, "_run") as run:
            self.assertEqual(bootstick.ventoy(self.vault, self.by_name()["sdb"], yes=True), 1)
            self.assertIn("genesis fetch --id ventoy", self.said[-1])
            open(os.path.join(self.vault, "tools", "ventoy-1.1.18-linux.tar.gz"), "w").close()
            self.assertEqual(bootstick.ventoy(self.vault, self.by_name()["sdb"], yes=True), 1)
            self.assertIn("No system on the drive fits", self.said[-1])
        run.assert_not_called()

    def test_nothing_is_erased_without_a_yes(self):
        self.iso("debian-live-13.7.0-amd64-xfce.iso", 3000)
        open(os.path.join(self.vault, "tools", "ventoy-1.1.18-linux.tar.gz"), "w").close()
        with mock.patch.object(bootstick, "_run") as run, mock.patch.object(bootstick.sys.stdin, "isatty", return_value=True), \
                mock.patch("builtins.input", return_value="/dev/sdx"):
            self.assertEqual(bootstick.ventoy(self.vault, self.by_name()["sdb"]), 1)
        run.assert_not_called()
        self.assertEqual(self.said[-1], "Nothing was written.")


@unittest.skipIf(os.name == "nt", "disks are found the way Linux lists them")
class CardTests(Machine):
    def card(self, size):
        path = os.path.join(self.top, "card")
        with open(path, "wb") as f:
            f.write(b"\xff" * size)
        return {"name": "sdb", "path": path, "bytes": size, "parts": [], "mounted": [], "numbers": set(), "removable": True,
                "usb": True, "card": False, "model": "reader"}

    def image(self, lite=False, size=3 * 1024 * 1024 + 17):
        body = os.urandom(1024) * (size // 1024) + b"x" * (size % 1024)
        name = "2026-10-06-raspios-trixie-arm64%s.img.xz" % ("-lite" if lite else "")
        with lzma.open(os.path.join(self.vault, "isos", name), "wb", preset=0) as f:
            f.write(body)
        return body

    def test_the_system_is_written_to_the_card_and_read_back(self):
        body, disk = self.image(), self.card(8 * 1024 * 1024)
        self.image(lite=True, size=1024)
        with mock.patch.object(bootstick, "_run", return_value=0):
            self.assertEqual(bootstick.pi_card(self.vault, disk, yes=True), 0)
        with open(disk["path"], "rb") as f:
            self.assertEqual(f.read(len(body)), body, "the desktop system unless the small one is asked for")
        self.assertIn("read back and found right", self.said[-1])

    def test_a_card_that_does_not_read_back_right_is_called_bad(self):
        self.image()
        disk, real = self.card(8 * 1024 * 1024), open

        class Worn(io.FileIO):  # a card that takes what it is given and keeps something else
            def write(self, block):
                return super().write(b"\0" * len(block))

        def opened(path, mode="r", *a, **kw):
            return Worn(path, mode) if path == disk["path"] and "w" in mode else real(path, mode, *a, **kw)
        with mock.patch.object(bootstick, "_run", return_value=0), mock.patch("builtins.open", side_effect=opened):
            self.assertEqual(bootstick.pi_card(self.vault, disk, yes=True), 1)
        self.assertIn("did not read back as it was written", self.said[-1])

    def test_a_card_too_small_for_the_system_is_said_to_be(self):
        self.image()
        with mock.patch.object(bootstick, "_run", return_value=0):
            self.assertEqual(bootstick.pi_card(self.vault, self.card(1024 * 1024), yes=True), 1)
        self.assertIn("too small", self.said[-1])

    def test_without_the_system_on_the_drive_it_says_how_to_bring_it(self):
        self.assertEqual(bootstick.pi_card(self.vault, self.card(1024), lite=True, yes=True), 1)
        self.assertIn("genesis fetch --id raspios_lite", self.said[-1])


if __name__ == "__main__":
    unittest.main()
