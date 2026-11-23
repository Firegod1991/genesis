"""The safe: one locked place on the drive for a family's own papers.

Identity papers, deeds, medical records, pictures of what cannot be replaced. On the drive they are
a single 7-Zip archive, encrypted with AES 256, the names of the files hidden with the rest. Open,
they are an ordinary folder that lives in the memory of the machine and never on a disk. Locking the
safe packs that folder back into the archive, proves the new archive opens, and wipes the folder.

Genesis asks for the passphrase and hands it to 7-Zip on its standard input. It is never stored and
never put on a command line, where other programs could read it. Nothing can recover it either: a
forgotten passphrase means the papers are gone. The archive is an ordinary .7z file, so any computer
with 7-Zip opens it with the same passphrase, with or without Genesis.
"""

import getpass
import os
import shutil
import subprocess
import sys
import tempfile
import time

from . import box
from .util import human, log

FOLDER = os.path.join("files", "safe")
ARCHIVE, BEFORE, NEW = "safe.7z", "safe.7z.bak", "safe.7z.new"
SHORTEST = 8
README = """The safe on this drive
======================

safe.7z      your papers, encrypted with AES 256. The names of the files are hidden too.
safe.7z.bak  the safe as it was before it was last locked. It opens with the passphrase it had then.

Open and lock it with the Genesis Safe icon at the top of the drive, or with:

    genesis safe

Without Genesis, any computer with 7-Zip opens safe.7z with the same passphrase.
Nobody can recover a forgotten passphrase. Write it down and keep it somewhere that is not this drive.
"""


def folder(vault):
    return os.path.join(vault, FOLDER)


def archive(vault):
    return os.path.join(folder(vault), ARCHIVE)


def workdir():
    """Where the open safe lives, and whether that place is memory. On Linux it is: nothing readable
    ever touches a disk, and a machine that loses power takes the open folder with it."""
    named = os.environ.get("GENESIS_SAFE_OPEN")
    if named:
        return os.path.abspath(os.path.expanduser(named)), False
    who = os.getuid() if hasattr(os, "getuid") else "box"
    for base in ("/dev/shm", os.environ.get("XDG_RUNTIME_DIR")):
        if base and os.path.isdir(base) and os.access(base, os.W_OK):
            return os.path.join(base, "genesis-safe-%s" % who), True
    return os.path.join(tempfile.gettempdir(), "genesis-safe-%s" % who), False


def seven_zip(vault, unpack=True):
    """The 7-Zip program for this machine: the one the drive carries, or one already installed."""
    own = os.path.join(box.runtime(vault), "7zip")
    places = [os.path.join(own, name) for name in ("7zzs", "7zz", "7zz.exe", "7z.exe")]
    places += [os.path.join(os.environ.get(var, ""), "7-Zip", "7z.exe") for var in ("ProgramFiles", "ProgramFiles(x86)")]
    for path in places:
        if os.path.isfile(path):
            return path
    for name in ("7zz", "7z", "7za"):
        found = shutil.which(name)
        if found:
            return found
    if unpack and os.path.isdir(os.path.join(vault, "tools")):
        try:  # a drive that carries 7-Zip and has not been made ready: unpacking it needs nothing but Python
            box.unpack_programs(vault)
        except OSError:
            return None
        return seven_zip(vault, unpack=False)
    return None


def _seven(exe, args, secret, cwd=None):
    """Run 7-Zip with the passphrase on its standard input. Returns its exit code and what it said."""
    done = subprocess.run((exe if isinstance(exe, list) else [exe]) + args, input=(secret + "\n").encode("utf-8"),
                          stdout=subprocess.PIPE, stderr=subprocess.STDOUT, cwd=cwd)
    return done.returncode, done.stdout.decode("utf-8", "replace").strip()


def _opens(exe, path, secret):
    # No -p here: given a bare -p, 7-Zip takes the passphrase to be empty when it opens an archive.
    return _seven(exe, ["t", "-bso0", "-bsp0", path], secret)[0] == 0


def _ask(prompt):
    return getpass.getpass(prompt)


def _choose():
    """A new passphrase, typed twice. None when the person gives up."""
    for _ in range(3):
        first = _ask("Choose a passphrase for the safe (%d characters or more, a few words is best): " % SHORTEST)
        if len(first) < SHORTEST:
            log("That is too short.")
        elif first != _ask("Type it once more: "):
            log("Those two did not match.")
        else:
            if not first.isascii():
                log("Note: it has letters that some keyboards cannot type. Make sure you can type it anywhere.")
            return first
    return None


def _room(path):
    """Free space where a folder would be made."""
    path = os.path.dirname(path)
    while not os.path.exists(path):
        path = os.path.dirname(path)
    return shutil.disk_usage(path).free


def _contents(path):
    """Every file and link in the open folder, as (files, bytes)."""
    files = size = 0
    for here, _, names in os.walk(path):
        for name in names:
            files += 1
            try:
                size += os.lstat(os.path.join(here, name)).st_size
            except OSError:
                pass
    return files, size


def _wipe(path, memory):
    """Remove the open folder. Where it was on a disk, its files are written over first, which helps on
    a hard disk and cannot be promised on flash."""
    if not memory:
        for here, _, names in os.walk(path):
            for name in names:
                target = os.path.join(here, name)
                try:
                    if os.path.isfile(target) and not os.path.islink(target):
                        with open(target, "r+b") as f:
                            left = os.path.getsize(target)
                            while left > 0:
                                step = min(left, 1024 * 1024)
                                f.write(b"\0" * step)
                                left -= step
                            f.flush()
                            os.fsync(f.fileno())
                except OSError:
                    pass
    shutil.rmtree(path, ignore_errors=True)
    return not os.path.exists(path)


def _show(path):
    """Open the folder in the file manager, when there is a screen to show it on."""
    try:
        if os.name == "nt":
            os.startfile(path)
        elif sys.platform == "darwin":
            subprocess.Popen(["open", path], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        elif (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")) and shutil.which("xdg-open"):
            subprocess.Popen(["xdg-open", path], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                             start_new_session=True)
    except OSError:
        pass


def status(vault):
    """What can be said about the safe without its passphrase."""
    path, (work, _) = archive(vault), workdir()
    out = {"exists": os.path.isfile(path), "open": os.path.isdir(work), "program": bool(seven_zip(vault, unpack=False))}
    if out["exists"]:
        out.update(bytes=os.path.getsize(path), locked=int(os.path.getmtime(path)))
    return out


def _no_program():
    log("7-Zip is needed for the safe and was not found. The tools pack carries it: run `genesis fetch --id sevenzip`, "
        "then `genesis ready`. Or install 7-Zip on this machine.")
    return 1


def open_safe(vault):
    exe = seven_zip(vault)
    if not exe:
        return _no_program()
    path, (work, memory) = archive(vault), workdir()
    if os.path.isdir(work):
        log("The safe is already open: %s" % work)
        log("Lock it when you are done: genesis safe lock")
        _show(work)
        return 0
    if os.path.isfile(path):
        room = _room(work)
        if os.path.getsize(path) > room:
            log("The safe holds %s and only %s is free where it would open. Free some memory, or name a folder "
                "to open it in with GENESIS_SAFE_OPEN." % (human(os.path.getsize(path)), human(room)))
            return 1
        for _ in range(3):
            secret = _ask("Passphrase for the safe: ")
            os.makedirs(work, mode=0o700)
            code, said = _seven(exe, ["x", "-y", "-bso0", "-bsp0", "-o" + work, path], secret)
            if code == 0:
                break
            _wipe(work, memory)
            log("That passphrase does not open the safe." if "password" in said.lower()
                else "7-Zip could not open the safe: %s" % (said.splitlines() or ["no reason given"])[-1])
        else:
            log("The safe stays locked. Nothing was changed.")
            return 1
        files, size = _contents(work)
        log("The safe is open: %s (%d files, %s)" % (work, files, human(size)))
    else:
        os.makedirs(work, mode=0o700)
        log("A new safe. Put your papers in this folder: %s" % work)
        log("You choose its passphrase when you lock it for the first time.")
    if not memory:
        log("On this machine the open folder is on a disk, not in memory. Lock the safe as soon as you are done.")
    else:
        log("The open folder is in memory. Lock the safe before you switch off, or what you changed is lost.")
    log("To lock it: genesis safe lock")
    _show(work)
    return 0


def lock_safe(vault, empty=False, renew=False):
    exe = seven_zip(vault)
    if not exe:
        return _no_program()
    path, (work, memory) = archive(vault), workdir()
    if not os.path.isdir(work):
        log("The safe is not open, so there is nothing to lock.")
        return 0
    files, size = _contents(work)
    had = os.path.isfile(path)
    if not files:
        if had and not empty:
            log("The open folder is empty. Locking it would replace your papers with nothing, so nothing was changed.")
            log("To close it and keep the safe as it was: genesis safe lock --empty")
            return 1
        _wipe(work, memory)
        log("The folder was empty. It is closed, and the safe on the drive is as it was." if had
            else "Nothing was put in, so no safe was made.")
        return 0
    if had:
        for _ in range(3):
            secret = _ask("Passphrase for the safe: ")
            if _opens(exe, path, secret):
                break
            log("That is not the passphrase this safe was locked with.")
        else:
            log("The safe stays open and unchanged. To lock it under a new passphrase you need the old one first.")
            return 1
        if renew:
            secret = _choose()
            if secret is None:
                log("No new passphrase was chosen. The safe stays open and unchanged.")
                return 1
    else:
        secret = _choose()
        if secret is None:
            log("No passphrase was chosen. The safe stays open: your papers are still in %s" % work)
            return 1
    os.makedirs(folder(vault), exist_ok=True)
    new = os.path.join(folder(vault), NEW)
    if os.path.exists(new):
        os.remove(new)
    code, said = _seven(exe, ["a", "-t7z", "-mhe=on", "-mx=3", "-bso0", "-bsp0", "-p", new, "."], secret, cwd=work)
    if code != 0 or not os.path.isfile(new) or not _opens(exe, new, secret):
        if os.path.exists(new):
            os.remove(new)
        log("The safe could not be written (%s). It stays open: your papers are still in %s"
            % ((said.splitlines() or ["7-Zip gave no reason"])[-1], work))
        return 1
    try:
        os.chmod(new, 0o600)
    except OSError:
        pass
    if had:
        os.replace(path, os.path.join(folder(vault), BEFORE))  # one step back, should a lock ever go wrong
    os.replace(new, path)
    note = os.path.join(folder(vault), "READ ME.txt")
    if not os.path.isfile(note):
        with open(note, "w", encoding="utf-8", newline="\n") as f:
            f.write(README)
    if not _wipe(work, memory):
        log("The safe is locked, and the open folder could not be removed. Delete it yourself: %s" % work)
        return 1
    log("The safe is locked: %d files, %s, in %s" % (files, human(os.path.getsize(path)), path))
    return 0


def main(args):
    from .fetch import vault_path
    vault = vault_path(args)
    action = getattr(args, "action", None)
    if action == "status":
        now = status(vault)
        if now["open"]:
            log("The safe is open: %s" % workdir()[0])
        elif now["exists"]:
            log("The safe is locked: %s, last locked %s" % (human(now["bytes"]), time.strftime("%Y-%m-%d %H:%M", time.localtime(now["locked"]))))
        else:
            log("There is no safe on this drive yet. Start one with: genesis safe open")
        return 0
    if action == "open":
        return open_safe(vault)
    if action == "lock":
        return lock_safe(vault, empty=getattr(args, "empty", False), renew=getattr(args, "new_passphrase", False))
    # No action named: the icon on the drive. Open the safe when it is shut, offer to lock it when it is open.
    if os.path.isdir(workdir()[0]):
        log("The safe is open: %s" % workdir()[0])
        answer = input("Lock it now? [Y/n] ").strip().lower() if sys.stdin.isatty() else "y"
        return lock_safe(vault) if answer in ("", "y", "yes") else 0
    return open_safe(vault)
