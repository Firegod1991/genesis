"""A way back in: a USB stick that starts a computer, and a card that starts a Raspberry Pi.

The drive opens on Linux. A computer whose own system is dead, or one that only has Windows on it,
needs something to start from before it can read the drive. `genesis bootstick` makes that from what
the drive already carries:

* For a PC: Ventoy on a USB stick, with the live systems from the drive copied on beside it. Start the
  PC from the stick, pick a system, plug in the drive, and Start Genesis runs as it does anywhere.
* For a Raspberry Pi (`--pi`): Raspberry Pi OS written to a spare card, read back and compared, for the
  day the card in the Pi wears out.

Both erase the stick or card they are given. Nothing is written until the disk has been checked: it has
to be a whole disk, plugged in over USB or a card reader, that holds neither this drive nor the system
this machine is running from. Linux only, and it needs root.
"""

import hashlib
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time

from .util import human, log

SYS, MOUNTS = "/sys/block", "/proc/mounts"
VENTOY = re.compile(r"^ventoy-[\w.]+-linux\.tar\.gz$")
PI_OS = re.compile(r"raspios.*arm64(-lite)?\.img\.xz$")
# The systems worth having on a stick, most useful first. The first one is a whole desktop with
# Python on it, so Genesis starts from the drive as soon as that system is up.
SYSTEMS = (r"^debian-live-.*amd64.*\.iso$", r"^clonezilla-live-.*amd64\.iso$", r"^gparted-live-.*amd64\.iso$",
           r"^rescuezilla-.*\.iso$", r"^ubuntu-.*-desktop-amd64\.iso$", r"^linuxmint-.*\.iso$",
           r"^tails-amd64-.*\.img$", r"^debian-.*-amd64-netinst\.iso$", r"^alpine-.*x86_64\.iso$",
           r"^archlinux-.*x86_64\.iso$", r"^proxmox-ve.*\.iso$", r"^ubuntu-.*-live-server-amd64\.iso$")
SPARE = 64 * 1024 * 1024
README = """This stick starts a computer
============================

It was made by Genesis with Ventoy. The systems on it are the files in this folder.

1. Plug the stick into the computer and switch it on.
2. As it starts, press the key that opens its boot menu. On most machines that is F12, F10, F9, F2 or Esc.
3. Pick the USB stick. A list of the systems on it appears.
4. Pick the first one for a full desktop. It runs from the stick and changes nothing on the computer.
5. Plug in the Genesis drive, open it in the file manager and double click Start Genesis.

If the stick does not show in the boot menu, look in the computer's setup screen for Secure Boot and
switch it off, or enroll the Ventoy key when the stick offers to.

To add a system, copy its .iso file onto the stick. To remove one, delete it. Nothing else is needed.
"""


def _text(path):
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return f.read().strip()
    except OSError:
        return ""


def _mounted():
    """Where every device is mounted, by device path."""
    out = {}
    for line in _text(MOUNTS).splitlines():
        parts = line.split()
        if len(parts) >= 2:
            where = re.sub(r"\\([0-7]{3})", lambda m: chr(int(m.group(1), 8)), parts[1])
            out.setdefault(os.path.realpath(parts[0]) if parts[0].startswith("/") else parts[0], []).append(where)
    return out


def _parts(name):
    """The partitions of a disk, by name."""
    base = os.path.join(SYS, name)
    return sorted(p for p in os.listdir(base) if p.startswith(name) and os.path.exists(os.path.join(base, p, "partition")))


def disks():
    """Every whole disk this machine sees, with what is needed to tell a stick from a drive that matters."""
    out, mounted = [], _mounted()
    for name in sorted(os.listdir(SYS)) if os.path.isdir(SYS) else []:
        if name.startswith(("loop", "ram", "zram", "dm-", "md", "sr", "fd", "nbd")):
            continue
        base = os.path.join(SYS, name)
        try:
            size = int(_text(os.path.join(base, "size")) or 0) * 512
        except ValueError:
            size = 0
        if not size:
            continue  # a card reader with no card in it
        parts = _parts(name)
        numbers = {_text(os.path.join(base, "dev"))} | {_text(os.path.join(base, p, "dev")) for p in parts}
        where = [m for dev in [name] + parts for m in mounted.get("/dev/" + dev, [])]
        out.append({
            "name": name, "path": "/dev/" + name, "bytes": size, "parts": parts, "mounted": where, "numbers": numbers,
            "removable": _text(os.path.join(base, "removable")) == "1",
            "usb": "/usb" in os.path.realpath(base),
            "card": name.startswith("mmcblk"),
            "model": " ".join((_text(os.path.join(base, "device", "vendor")) + " " + _text(os.path.join(base, "device", "model"))).split())
                     or _text(os.path.join(base, "device", "name")) or "disk"})
    return out


def _number(path):
    """The device a path lives on, as the major:minor the kernel lists."""
    try:
        dev = os.stat(path).st_dev
    except OSError:
        return None
    return "%d:%d" % (os.major(dev), os.minor(dev))


def refusal(disk, keep, force=False):
    """Why this disk must not be erased, or None when it may be. `keep` are paths that have to survive:
    the vault, this code and the root of the running system."""
    for path in keep:
        if _number(path) in disk["numbers"]:
            return "%s holds %s" % (disk["path"], path)
    for where in disk["mounted"]:
        inside = where.rstrip("/") + "/"
        if where in ("/", "/boot", "/boot/firmware", "/boot/efi") or any((os.path.abspath(k) + "/").startswith(inside) for k in keep):
            return "%s is in use by this system (%s)" % (disk["path"], where)
    if not (disk["removable"] or disk["usb"] or disk["card"]) and not force:
        return "%s is not a USB stick or a card. If it really is the one to erase, add --force" % disk["path"]
    if disk["bytes"] > 300e9 and not force:
        return "%s holds %s, which is big for a stick. If it really is the one to erase, add --force" % (disk["path"], human(disk["bytes"]))
    return None


def _describe(d):
    kind = "card" if d["card"] else "USB" if d["usb"] else "removable" if d["removable"] else "fixed"
    return "%-14s %9s  %-9s %s%s" % (d["path"], human(d["bytes"]), kind, d["model"],
                                      ("  (mounted: %s)" % ", ".join(d["mounted"])) if d["mounted"] else "")


def _run(cmd, **kw):
    return subprocess.call(cmd, **kw)


def _unmount(disk):
    for part in [disk["name"]] + disk["parts"]:
        if _mounted().get("/dev/" + part):
            if _run(["umount", "/dev/" + part]) != 0:
                return False
    return True


def _confirm(disk, what, yes):
    log("")
    log("This erases everything on %s" % _describe(disk).strip())
    log("and makes it %s." % what)
    if yes:
        return True
    if not sys.stdin.isatty():
        log("Nothing was written. Run it in a terminal to confirm, or add --yes.")
        return False
    return input("Type the name of the disk to go on (%s): " % disk["path"]).strip() == disk["path"]


def _first_partition(disk, wait=20):
    """The first partition of a disk that has just been given a new table."""
    for _ in range(wait * 2):
        for part in (disk["name"] + "1", disk["name"] + "p1"):
            if os.path.exists("/dev/" + part):
                return "/dev/" + part
        time.sleep(0.5)
    return None


def choose(folder, free, named=None):
    """The systems to copy, in order, for the room there is. Named ones first and only those when given."""
    have = sorted(n for n in os.listdir(folder) if os.path.isfile(os.path.join(folder, n))) if os.path.isdir(folder) else []
    if named:
        wanted = [n for word in named for n in have if word.lower() in n.lower()]
    else:
        wanted = [n for pattern in SYSTEMS for n in have if re.search(pattern, n)]
    out, left = [], free - SPARE
    for name in dict.fromkeys(wanted):
        size = os.path.getsize(os.path.join(folder, name))
        if size <= left:
            out.append(name)
            left -= size
    return out


def _copy(src, dst):
    with open(src, "rb") as a, open(dst, "wb") as b:
        shutil.copyfileobj(a, b, 4 * 1024 * 1024)
        b.flush()
        os.fsync(b.fileno())


def ventoy(vault, disk, named=None, yes=False):
    """Ventoy on the stick, then the live systems copied beside it."""
    tools = os.path.join(vault, "tools")
    packed = sorted(n for n in os.listdir(tools) if VENTOY.match(n)) if os.path.isdir(tools) else []
    if not packed:
        log("Ventoy is not on the drive. Bring it with: genesis fetch --id ventoy --platform linux-x64")
        return 1
    isos = os.path.join(vault, "isos")
    if not choose(isos, disk["bytes"], named):
        log("No system on the drive fits this stick%s. Bring some with: genesis fetch --pack isos"
            % (" under that name" if named else ""))
        return 1
    if not _confirm(disk, "a stick that starts a PC", yes):
        log("Nothing was written.")
        return 1
    if not _unmount(disk):
        log("%s is in use and would not unmount. Close what is using it and try again." % disk["path"])
        return 1
    work = tempfile.mkdtemp(prefix="genesis-ventoy-")
    try:
        with tarfile.open(os.path.join(tools, packed[-1])) as t:
            try:
                t.extractall(work, filter="tar")
            except TypeError:  # Python before 3.12 has no filters
                t.extractall(work)
        script = next((os.path.join(here, "Ventoy2Disk.sh") for here, _, names in os.walk(work) if "Ventoy2Disk.sh" in names), None)
        if not script:
            log("%s does not hold Ventoy2Disk.sh" % packed[-1])
            return 1
        log("Installing Ventoy. It asks twice before it writes: answer y both times.")
        # Ventoy asks for itself on a terminal. With --yes its two questions are answered here.
        feed = {"input": b"y\ny\n"} if yes else {}
        done = subprocess.run(["sh", script, "-I", disk["path"]], cwd=os.path.dirname(script), **feed)
        if done.returncode != 0:
            log("Ventoy did not finish (exit %d). The stick may be half written: run this again." % done.returncode)
            return 1
        part = _first_partition(disk)
        if not part:
            log("Ventoy is installed, and its partition did not show up. Unplug the stick, plug it back in and copy "
                "the systems from %s onto it by hand." % isos)
            return 1
        mount = os.path.join(work, "stick")
        os.makedirs(mount)
        if _run(["mount", part, mount]) != 0:
            log("Ventoy is installed, and this machine cannot open its exFAT partition. Copy the systems from %s "
                "onto the stick on a machine that can." % isos)
            return 1
        try:
            names = choose(isos, shutil.disk_usage(mount).free, named)
            for n, name in enumerate(names, 1):
                log("[%d/%d] %s (%s)" % (n, len(names), name, human(os.path.getsize(os.path.join(isos, name)))))
                _copy(os.path.join(isos, name), os.path.join(mount, name))
            with open(os.path.join(mount, "READ ME.txt"), "w", encoding="utf-8", newline="\r\n") as f:
                f.write(README)
        finally:
            _run(["sync"])
            _run(["umount", mount])
        log("The stick is ready: %d systems on it. Its own READ ME says how to start a computer from it." % len(names))
        return 0
    finally:
        shutil.rmtree(work, ignore_errors=True)


def pi_card(vault, disk, lite=False, yes=False):
    """Raspberry Pi OS written straight onto a card, then read back and compared."""
    import lzma
    isos = os.path.join(vault, "isos")
    found = sorted(n for n in os.listdir(isos) if PI_OS.search(n)) if os.path.isdir(isos) else []
    found = [n for n in found if n.endswith("-lite.img.xz") == lite]
    if not found:
        log("Raspberry Pi OS%s is not on the drive. Bring it with: genesis fetch --id %s"
            % (" Lite" if lite else "", "raspios_lite" if lite else "raspios_desktop"))
        return 1
    image = os.path.join(isos, found[-1])
    if not _confirm(disk, "a card that starts a Raspberry Pi (%s)" % found[-1], yes):
        log("Nothing was written.")
        return 1
    if not _unmount(disk):
        log("%s is in use and would not unmount. Close what is using it and try again." % disk["path"])
        return 1
    wrote, digest, last = 0, hashlib.sha256(), time.time()
    log("Writing %s to %s ..." % (found[-1], disk["path"]))
    with lzma.open(image) as src, open(disk["path"], "wb", buffering=0) as dst:
        while True:
            block = src.read(4 * 1024 * 1024)
            if not block:
                break
            if wrote + len(block) > disk["bytes"]:
                log("The card is too small for this system. It is not usable as it is: use a bigger card.")
                return 1
            dst.write(block)
            digest.update(block)
            wrote += len(block)
            if time.time() - last > 15:
                last = time.time()
                log("  %s written" % human(wrote))
        os.fsync(dst.fileno())
    log("Reading it back ...")
    check, left = hashlib.sha256(), wrote
    with open(disk["path"], "rb", buffering=0) as back:
        try:  # read the card itself, not the copy this machine still has in memory
            os.posix_fadvise(back.fileno(), 0, 0, os.POSIX_FADV_DONTNEED)
        except (AttributeError, OSError):
            pass
        while left > 0:
            block = back.read(min(4 * 1024 * 1024, left))
            if not block:
                break
            check.update(block)
            left -= len(block)
    if left or check.hexdigest() != digest.hexdigest():
        log("The card did not read back as it was written. It is worn out or the reader is faulty: use another card.")
        return 1
    _run(["sync"])
    log("The card is ready: %s, read back and found right. Put it in the Pi and switch on." % human(wrote))
    return 0


def main(args):
    from .fetch import vault_path
    from .util import ROOT
    vault = vault_path(args)
    if not sys.platform.startswith("linux"):
        log("This runs on Linux. On Windows, Rufus writes a stick; on a Mac, Raspberry Pi Imager does. "
            "The systems to write are in %s" % os.path.join(vault, "isos"))
        return 1
    found, keep = disks(), [vault, ROOT, "/"]
    if not getattr(args, "device", None):
        ok = [d for d in found if not refusal(d, keep)]
        if not ok:
            log("No USB stick or card was found. Plug one in and run this again. Everything on it will be erased.")
            return 1
        log("Sticks and cards this machine sees:")
        for d in ok:
            log("  " + _describe(d))
        log("")
        log("Everything on the one you pick is erased. To go on:")
        log('  sudo sh "%s" bootstick --device %s%s' % (os.path.join(ROOT, "bin", "genesis"), ok[0]["path"],
                                                         " --pi" if getattr(args, "pi", False) else ""))
        return 0
    path = os.path.realpath(args.device)
    disk = next((d for d in found if d["path"] == path), None)
    if not disk:
        whole = next((d for d in found if os.path.basename(path) in d["parts"]), None)
        log("%s is one part of %s. Name the whole disk: --device %s" % (args.device, whole["path"], whole["path"]) if whole
            else "%s is not a disk this machine sees." % args.device)
        return 1
    why = refusal(disk, keep, force=getattr(args, "force", False))
    if why:
        log("Not touching it: %s." % why)
        return 1
    if hasattr(os, "geteuid") and os.geteuid() != 0:
        log("Writing a disk needs root. Run it again with sudo in front.")
        return 1
    if getattr(args, "pi", False):
        return pi_card(vault, disk, lite=getattr(args, "lite", False), yes=getattr(args, "yes", False))
    return ventoy(vault, disk, named=getattr(args, "iso", None), yes=getattr(args, "yes", False))
