"""The box with no Docker: the dashboard, the Kiwix reader and the local models, started straight
from what is on the drive.

A Raspberry Pi with nothing but the drive, a screen and a keyboard runs this. `genesis ready`
is done once, while there is still a connection or another machine to do it on: it unpacks the
programs the drive carries, lists the library and draws the covers. `genesis start` needs
nothing but Python and the drive.
"""

import http.client
import os
import re
import shutil
import signal
import socket
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.request
import webbrowser
import zipfile

from . import catalog
from .util import ROOT, log, read_json, write_json

DASHBOARD_PORT, KIWIX_PORT, OLLAMA_PORT = 8080, 8081, 11434
SPARE_PORTS = (8090, 8765, 8780)  # for the dashboard, when another program on the machine holds its usual port
LIBRARY = os.path.join(".genesis", "library.xml")
# How the archives in <vault>/tools name each machine, and what Genesis calls it.
KIWIX = re.compile(r"^kiwix-tools_(linux-aarch64|linux-x86_64|macos-arm64|macos-x86_64|win-x86_64)"
                   r"-[\d.]+(?:-\d+)?\.(?:tar\.gz|zip)$")
OLLAMA = re.compile(r"^ollama-linux-(arm64|amd64)\.tar\.zst$")
SEVEN = re.compile(r"^7z\d+-linux-(arm64|x64)\.tar\.xz$")
MACHINE = {"linux-aarch64": "linux-arm64", "linux-x86_64": "linux-x64", "arm64": "linux-arm64", "amd64": "linux-x64",
           "x64": "linux-x64", "macos-arm64": "macos-arm64", "macos-x86_64": "macos-x64", "win-x86_64": "windows-x64"}

DESKTOP = """[Desktop Entry]
Type=Application
Name=Start Genesis
Comment=Maps, manuals, the library and the assistant, with no internet
Exec=sh -c 'exec sh "$(dirname "$1")/genesis/bin/genesis" start' genesis %%k
Icon=%s
Terminal=false
Categories=Education;
"""
START = """#!/bin/sh
# Start Genesis from this drive. Needs nothing but the drive and Python 3, which Raspberry Pi OS has.
exec sh "$(dirname "$0")/genesis/bin/genesis" start "$@"
"""
SAFE_DESKTOP = """[Desktop Entry]
Type=Application
Name=Genesis Safe
Comment=Your own papers, locked behind a passphrase
Exec=sh -c 'exec sh "$(dirname "$1")/Genesis Safe.sh"' genesis %%k
Icon=%s
Terminal=true
Categories=Utility;
"""
SAFE = """#!/bin/sh
# Open the safe on this drive, or lock it when it is open: your own papers, kept behind a passphrase.
sh "$(dirname "$0")/genesis/bin/genesis" safe "$@"
printf '\\nPress Enter to close this window. '
read _
"""
GUIDE = """GENESIS: start here
===================

This drive is a library, a map of the planet, manuals, scripture, radio tools and an assistant
that runs on your own machine. None of it needs the internet. It runs on a Raspberry Pi or on
any Linux PC that has Python 3.

START IT
  Double click "Start Genesis" on this drive. If it asks, choose Execute.
  Or open a terminal in this folder, the top of the drive, and type:

      sh "Start Genesis.sh"

  In the file manager, a right click in this folder offers to open a terminal here.
  Every line on this page is typed in that terminal.

  The browser opens http://localhost:8080  That page is Genesis. Type what you need.
  The page is up in a few seconds. The book reader and the assistant follow within a minute.
  Phones and laptops on the same network can open port 8080 of this machine.

STOP IT
      sh genesis/bin/genesis stop

ON A RASPBERRY PI 5
  Use the official 27 W power supply. With a weaker one the Pi limits USB power and a hard
  disk may not spin up. A powered USB hub also works. Plug the drive into a blue USB port.

YOUR OWN PAPERS
  Double click "Genesis Safe" to keep identity papers, deeds and medical records on this
  drive behind a passphrase. Nobody can recover a forgotten passphrase.

IF THIS DRIVE IS ALL YOU HAVE LEFT
  It opens on Linux. A computer that runs Windows, or one whose own system is dead, has to be
  started from a boot stick first. Make one now, while you can, from any USB stick:

      sudo sh genesis/bin/genesis bootstick

  And a spare card for the Pi:

      sudo sh genesis/bin/genesis bootstick --pi

  Windows cannot read this drive and will offer to format it. Always answer no.

IF SOMETHING GOES WRONG
  Notes will not save: the drive belongs to another user. Run once:
      sudo chown -R $USER .
  The double click does nothing: use the terminal line under START IT.
  To check every file on the drive against its seal:
      sh genesis/bin/genesis verify --sealed

WHAT IS WHERE
  vault/     everything: books, maps, manuals, models, scripture, programs, systems
  genesis/   the program itself. Nothing in it needs the internet.
  More: genesis/README.md and genesis/docs/
"""
FOLDERS = {"zim": "book", "documents": "manual", "maps": "map", "scripture": "scripture", "models": "assistant",
           "tools": "tools", "apps": "phone", "isos": "disc", "radio": "radio", "poi": "pin", "code": "code",
           "study": "study", "restore": "seal", "runtime": "gear", "files": "note"}


def runtime(vault, platform=None):
    return os.path.join(vault, "runtime", platform or catalog.host_platform())


def _safe(base, name):
    target = os.path.normpath(os.path.join(base, name))
    if not (target + os.sep).startswith(os.path.abspath(base) + os.sep):
        raise ValueError("archive member escapes its folder: %s" % name)
    return target


def _unpack(members, into, strip=0):
    """Write the regular files of an open tar stream under `into`, dropping `strip` leading folders."""
    count = 0
    for m, src in members:
        parts = m.name.strip("/").split("/")[strip:]
        if not parts or not (m.isfile() or m.issym()):
            continue
        target = _safe(into, os.path.join(*parts))
        os.makedirs(os.path.dirname(target), exist_ok=True)
        if m.issym():  # libraries name each other through links: keep the ones that stay inside the folder
            _safe(into, os.path.join(os.path.dirname(os.path.join(*parts)), m.linkname))
            if os.path.lexists(target):
                os.remove(target)
            if os.name != "nt":
                os.symlink(m.linkname, target)
            continue
        with open(target + ".tmp", "wb") as dst:
            shutil.copyfileobj(src(m), dst, 1024 * 1024)
        os.chmod(target + ".tmp", 0o755 if m.mode & 0o111 else 0o644)
        os.replace(target + ".tmp", target)
        count += 1
    return count


def _unzip(path, into):
    """Write the files of a zip archive under `into`. The Kiwix server for Windows comes this way, with its
    libraries beside it and no folder around them."""
    count = 0
    with zipfile.ZipFile(path) as z:
        for info in z.infolist():
            if info.is_dir():
                continue
            target = _safe(into, info.filename)
            os.makedirs(os.path.dirname(target), exist_ok=True)
            with z.open(info) as src, open(target + ".tmp", "wb") as dst:
                shutil.copyfileobj(src, dst, 1024 * 1024)
            os.chmod(target + ".tmp", 0o755)
            os.replace(target + ".tmp", target)
            count += 1
    return count


def _fresh(folder, archive):
    """True when `folder` already holds what this archive unpacks to."""
    try:
        with open(os.path.join(folder, ".from"), encoding="utf-8") as f:
            return f.read().strip() == os.path.basename(archive)
    except OSError:
        return False


def _mark(folder, archive):
    with open(os.path.join(folder, ".from"), "w", encoding="utf-8") as f:
        f.write(os.path.basename(archive) + "\n")


def unpack_programs(vault):
    """Unpack the Kiwix server, Ollama and 7-Zip the drive carries, for every machine it carries them for."""
    tools, done = os.path.join(vault, "tools"), []
    for name in sorted(os.listdir(tools)) if os.path.isdir(tools) else []:
        path = os.path.join(tools, name)
        kiwix, ollama = KIWIX.match(name), OLLAMA.match(name)
        if kiwix:
            into = os.path.join(runtime(vault, MACHINE[kiwix.group(1)]), "kiwix")
            if not _fresh(into, path):
                shutil.rmtree(into, ignore_errors=True)
                os.makedirs(into)
                if name.endswith(".zip"):
                    _unzip(path, into)
                else:
                    with tarfile.open(path) as t:
                        _unpack(((m, t.extractfile) for m in t), into, strip=1)
                _mark(into, path)
                done.append("%s for %s" % ("the Kiwix server", MACHINE[kiwix.group(1)]))
        elif ollama:
            into = os.path.join(runtime(vault, MACHINE[ollama.group(1)]), "ollama")
            if _fresh(into, path):
                continue
            zstd = shutil.which("zstd") or shutil.which("unzstd")
            if not zstd:
                log("  zstd is not installed, so %s stays packed. Install zstd and run `genesis ready` again." % name)
                continue
            shutil.rmtree(into, ignore_errors=True)
            os.makedirs(into)
            proc = subprocess.Popen([zstd, "-dc", path], stdout=subprocess.PIPE)
            try:
                with tarfile.open(fileobj=proc.stdout, mode="r|") as t:
                    _unpack(((m, t.extractfile) for m in t), into)
            finally:
                proc.stdout.close()
                if proc.wait() != 0:
                    shutil.rmtree(into, ignore_errors=True)
                    raise OSError("zstd could not read %s" % name)
            _mark(into, path)
            done.append("Ollama for %s" % MACHINE[ollama.group(1)])
        elif SEVEN.match(name):
            machine = MACHINE[SEVEN.match(name).group(1)]
            into = os.path.join(runtime(vault, machine), "7zip")
            if _fresh(into, path):
                continue
            try:
                import lzma  # noqa: F401 - tarfile reads .xz through it, and a few small systems ship Python without it
            except ImportError:
                log("  this Python cannot read .xz, so %s stays packed" % name)
                continue
            shutil.rmtree(into, ignore_errors=True)
            os.makedirs(into)
            with tarfile.open(path) as t:  # the program in its two builds and its license: the manual stays packed
                _unpack(((m, t.extractfile) for m in t if m.name in ("7zz", "7zzs", "License.txt")), into)
            _mark(into, path)
            done.append("7-Zip for %s" % machine)
    return done


def program(vault, kind, name):
    """A program for this machine: the one unpacked from the drive, or one already installed."""
    exe = name + (".exe" if os.name == "nt" else "")
    for folder in (os.path.join(runtime(vault), kind), os.path.join(runtime(vault), kind, "bin")):
        if os.path.isfile(os.path.join(folder, exe)):
            return os.path.join(folder, exe)
    return shutil.which(name)


REDIST = "https://learn.microsoft.com/cpp/windows/latest-supported-vc-redist"


def reader_runs():
    """On Windows the Kiwix server needs Microsoft's Visual C++ runtime. Most PCs have it, because other
    programs brought it, and a freshly installed one may not. Asking first keeps Windows from putting up
    an error box for every book. Everywhere else the answer is yes."""
    if os.name != "nt":
        return True
    try:
        import ctypes
        ctypes.WinDLL("msvcp140.dll")
        ctypes.WinDLL("vcruntime140_1.dll")
    except (ImportError, OSError):
        return False
    return True


def books(vault):
    folder = os.path.join(vault, "zim")
    return sorted(os.path.join(folder, n) for n in os.listdir(folder) if n.endswith(".zim")) if os.path.isdir(folder) else []


def _shelf_mark(shelf):
    return [[os.path.basename(path), os.path.getsize(path)] for path in shelf]


def library(vault, shelf):
    """The list of books for the Kiwix server, when it still matches what is on the shelf."""
    path = os.path.join(vault, LIBRARY)
    made = read_json(path + ".json") if os.path.isfile(path) else None
    return path if isinstance(made, dict) and made.get("shelf") == _shelf_mark(shelf) else None


def list_books(vault):
    """Write the list of books the Kiwix server reads when it starts. Given hundreds of books by name it
    opens every one before it answers, which takes minutes on a hard disk; given the list it answers at once.
    The list names each book by a path beside itself, so it holds wherever the drive is plugged in.
    Returns how many books are listed, or None where no Kiwix program runs on this machine."""
    manage, shelf = program(vault, "kiwix", "kiwix-manage"), books(vault)
    if not manage or not shelf or not reader_runs():
        return None
    path = os.path.join(vault, LIBRARY)
    if library(vault, shelf):
        return read_json(path + ".json")["listed"]
    tmp, listed = path + ".tmp", 0
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if os.path.exists(tmp):
        os.remove(tmp)

    def add(some):
        return subprocess.call([manage, tmp, "add"] + some, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL) == 0
    for i in range(0, len(shelf), 25):
        some = shelf[i:i + 25]
        if add(some):
            listed += len(some)
            continue
        for book in some:  # one of these is damaged: list the rest, and say which
            if add([book]):
                listed += 1
            else:
                log("  the Kiwix server cannot read %s, so it is left off the list" % os.path.basename(book))
    if not listed:
        return None
    os.replace(tmp, path)
    write_json(path + ".json", {"listed": listed, "shelf": _shelf_mark(shelf)})
    return listed


def draw_covers(vault):
    """The first page of every manual as a picture, so the shelves show covers. Needs pdftoppm."""
    tool = shutil.which("pdftoppm")
    if not tool:
        return None
    made, base = 0, os.path.join(vault, "documents")
    for folder, _, files in os.walk(base):
        for name in sorted(files):
            if not name.lower().endswith(".pdf"):
                continue
            rel = os.path.relpath(os.path.join(folder, name), vault)
            out = os.path.join(vault, ".genesis", "covers", rel[:-4])
            if os.path.isfile(out + ".jpg"):
                continue
            os.makedirs(os.path.dirname(out), exist_ok=True)
            rc = subprocess.call([tool, "-jpeg", "-singlefile", "-f", "1", "-l", "1", "-scale-to-x", "480", "-scale-to-y", "-1",
                                  os.path.join(folder, name), out], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            made += rc == 0
    return made


def drive_root(vault):
    """The folder that holds both this code and the vault, when they sit side by side on one drive."""
    parent = os.path.dirname(ROOT)
    return parent if os.path.dirname(os.path.abspath(vault)) == parent else None


def dress(vault):
    """Give the drive a front door, a page that says how to use it, and its folders their pictures. File
    managers on the Raspberry Pi read a folder's picture from a `.directory` file inside it, and want the full path."""
    icons = os.path.join(ROOT, "dashboard", "static", "icons")
    made = 0
    for name, icon in FOLDERS.items():
        folder, picture = os.path.join(vault, name), os.path.join(icons, icon + ".svg")
        if os.path.isdir(folder) and os.path.isfile(picture):
            with open(os.path.join(folder, ".directory"), "w", encoding="utf-8") as f:
                f.write("[Desktop Entry]\nIcon=%s\n" % picture)
            made += 1
    root = drive_root(vault)
    if root:
        with open(os.path.join(root, "Start Genesis.sh"), "w", encoding="utf-8", newline="\n") as f:
            f.write(START)
        with open(os.path.join(root, "Start Genesis.desktop"), "w", encoding="utf-8", newline="\n") as f:
            f.write(DESKTOP % os.path.join(icons, "genesis.svg"))
        with open(os.path.join(root, "Genesis Safe.sh"), "w", encoding="utf-8", newline="\n") as f:
            f.write(SAFE)
        with open(os.path.join(root, "Genesis Safe.desktop"), "w", encoding="utf-8", newline="\n") as f:
            f.write(SAFE_DESKTOP % os.path.join(icons, "lock.svg"))
        for name in ("Start Genesis.sh", "Start Genesis.desktop", "Genesis Safe.sh", "Genesis Safe.desktop"):
            os.chmod(os.path.join(root, name), 0o755)
        with open(os.path.join(root, "READ ME FIRST.txt"), "w", encoding="utf-8", newline="\n") as f:
            f.write(GUIDE)  # no path of this machine in it: the drive is made ready on one machine and read on another
        for name, icon in (("vault", "seal"), ("genesis", "gear")):
            if os.path.isdir(os.path.join(root, name)) and os.path.isfile(os.path.join(icons, icon + ".svg")):
                with open(os.path.join(root, name, ".directory"), "w", encoding="utf-8") as f:
                    f.write("[Desktop Entry]\nIcon=%s\n" % os.path.join(icons, icon + ".svg"))
    return made


def ready(args):
    from .fetch import vault_path
    vault = vault_path(args)
    os.makedirs(os.path.join(vault, ".genesis"), exist_ok=True)
    for what in unpack_programs(vault):
        log("unpacked " + what)
    listed = list_books(vault)
    if listed is not None:
        log("book list for the reader: %d books" % listed)
    covers = draw_covers(vault)
    log("covers: pdftoppm is not installed, so the shelves show titles" if covers is None else "covers drawn: %d new" % covers)
    log("folder pictures: %d" % dress(vault))
    have = [m for m in sorted(set(MACHINE.values()))
            if any(os.path.isfile(os.path.join(runtime(vault, m), "kiwix", "kiwix-serve" + ext)) for ext in ("", ".exe"))]
    log("%d books for the reader. Starts with no Docker on: %s" % (len(books(vault)), ", ".join(have) or "no machine yet"))
    return 0


# ------------------------------------------------------------------ start and stop

def _run_dir():
    base = os.environ.get("XDG_RUNTIME_DIR") or tempfile.gettempdir()
    path = os.path.join(base, "genesis-%s" % (os.getuid() if hasattr(os, "getuid") else "box"))
    os.makedirs(path, exist_ok=True)
    return path


def _listening(port):
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.5):
            return True
    except OSError:
        return False


def _ours(port):
    """True when what answers on this port is the Genesis dashboard, not another program that took the port."""
    try:
        with urllib.request.urlopen("http://127.0.0.1:%d/" % port, timeout=3) as answer:
            return b"<title>genesis</title>" in answer.read(4096).lower()
    except (OSError, ValueError, http.client.HTTPException):
        return False


def door(wanted):
    """The port the dashboard answers on: the usual one, unless another program on this machine holds it.
    Then the first spare port that is free, or where Genesis is already answering."""
    for port in (wanted,) + tuple(p for p in SPARE_PORTS if p != wanted):
        if not _listening(port) or _ours(port):
            return port
    return wanted


def _spawn(cmd, env, logname):
    out = open(os.path.join(_run_dir(), logname), "ab")
    kw = {"creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0)} if os.name == "nt" else {"start_new_session": True}
    return subprocess.Popen(cmd, env=env, stdin=subprocess.DEVNULL, stdout=out, stderr=subprocess.STDOUT, **kw).pid


def plan(vault):
    """What to start, as (name, port, command, extra environment). Only what this machine can run."""
    out = [("dashboard", int(os.environ.get("GENESIS_PORT", DASHBOARD_PORT)),
            [sys.executable, os.path.join(ROOT, "dashboard", "server.py")], {"GENESIS_BARE": "1"})]
    kiwix, shelf = program(vault, "kiwix", "kiwix-serve"), books(vault)
    if kiwix and shelf and reader_runs():
        listed = library(vault, shelf)
        out.append(("library", KIWIX_PORT, [kiwix, "--port", str(KIWIX_PORT)] + (["--library", listed] if listed else shelf), {}))
    ollama, models = program(vault, "ollama", "ollama"), os.path.join(vault, "models", "ollama", "models")
    if ollama and os.path.isdir(models):
        # Nothing on the drive is ever deleted by the model server, one model is in memory at a time,
        # and it stays there half an hour, because loading one from a hard disk takes a minute.
        out.append(("assistant", OLLAMA_PORT, [ollama, "serve"],
                    {"OLLAMA_MODELS": models, "OLLAMA_HOST": "127.0.0.1:%d" % OLLAMA_PORT, "OLLAMA_NOPRUNE": "1",
                     "OLLAMA_MAX_LOADED_MODELS": "1", "OLLAMA_KEEP_ALIVE": "30m"}))
    return out


def many_files():
    """Let the reader hold hundreds of books open at once. A Mac starts every program with room for 256
    open files and refuses a limit above what its kernel allows, so ask for less until one is granted.
    Returns the limit in force, or None on Windows, which has none to raise."""
    try:
        import resource
    except ImportError:
        return None
    soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
    for want in (65536, 10240, 4096):
        if hard != resource.RLIM_INFINITY:
            want = min(want, hard)
        if soft != resource.RLIM_INFINITY and want <= soft:
            break
        try:
            resource.setrlimit(resource.RLIMIT_NOFILE, (want, hard))
            return want
        except (ValueError, OSError):
            continue
    return soft


def nothing_yet(vault):
    """True when Genesis has never put anything in this vault: no record, no book, no manual."""
    return not (os.path.isfile(os.path.join(vault, ".genesis", "state.json")) or books(vault)
                or os.path.isdir(os.path.join(vault, "documents")))


def first(args, vault, ask=input):
    """The first start on a machine: offer to bring something down, so the shelves are not empty.
    Returns what was chosen: a tier, or None for empty shelves."""
    from .fetch import _free, fetch, load_config
    from .util import human
    cfg = load_config(vault)
    size = {tier: sum(catalog.entry_size(e, f) or 0 for _, _, e, f in
                      catalog.select(tier=tier, langs=cfg["langs"], platforms=cfg["platforms"]))
            for tier in (catalog.STARTER, "seed")}
    print("\nGenesis has nothing on this machine yet. What it brings down goes to\n  %s  (%s free)\n" % (vault, human(_free(vault))))
    print("  1  Starter  about %s. Every manual and plant guide, ten books with the best of Wikipedia," % human(size[catalog.STARTER]))
    print("              a map of the world and the Bible. Minutes on a fast line.")
    print("  2  Seed     about %s. The essentials of every pack. Hours." % human(size["seed"]))
    print("  3  Nothing now. Open Genesis with empty shelves.\n")
    print("For another drive, press Ctrl C, make a folder on it and run this again with --vault and that folder.")
    try:
        answer = ask("Type 1, 2 or 3 and press Enter [1]: ").strip() or "1"
    except EOFError:
        answer = "3"
    tier = {"1": catalog.STARTER, "2": "seed"}.get(answer)
    if not tier:
        return None
    args.tier, args.lang, args.platform, args.pack, args.id, args.tag, args.also = tier, None, None, None, None, None, None
    args.yes, args.keep_going, args.jobs = True, True, 4
    if fetch(args):
        log("Some of it did not come down. What is here stays. To get the rest, run: genesis fetch")
    ready(args)
    return tier


def start(args):
    from .fetch import vault_path
    vault = vault_path(args)
    # A folder named by hand that is not there is a drive that is not plugged in, never a place to fill.
    named = bool(getattr(args, "vault", None) or os.environ.get("GENESIS_VAULT"))
    fresh = nothing_yet(vault) and (os.path.isdir(vault) or not named)
    if fresh and sys.stdin and sys.stdin.isatty() and not getattr(args, "no_ask", False):
        first(args, vault)
    if fresh and not named:
        os.makedirs(os.path.join(vault, ".genesis"), exist_ok=True)  # nobody to ask, or nothing wanted: empty shelves
    if not os.path.isdir(vault):
        raise SystemExit("no vault at %s: plug in the drive, or make that folder first if Genesis should live there" % vault)
    if not program(vault, "kiwix", "kiwix-serve") and os.path.isdir(os.path.join(vault, "tools")):
        try:  # a drive filled without `genesis ready`: the Kiwix server unpacks with nothing but Python
            unpack_programs(vault)
        except OSError as e:
            log("could not unpack the programs on the drive: %s" % e)
    many_files()
    usual = int(os.environ.get("GENESIS_PORT", DASHBOARD_PORT))
    port = door(usual)
    if port != usual:
        log("another program on this machine is using port %d, so Genesis answers on port %d" % (usual, port))
        os.environ["GENESIS_PORT"] = str(port)
    state, missing = read_json(os.path.join(_run_dir(), "box.json"), {}), []
    for name, port, cmd, extra in plan(vault):
        if _listening(port):
            log("%s is already running on port %d" % (name, port))
            continue
        env = dict(os.environ, GENESIS_VAULT=vault, PORT=str(port), **extra)
        state[name] = _spawn(cmd, env, name + ".log")
        log("started %s on port %d" % (name, port))
    write_json(os.path.join(_run_dir(), "box.json"), state)
    started = {name for name, _, _, _ in plan(vault)}
    if "library" in started and not library(vault, books(vault)):
        log("the book list is out of date, so the reader opens every book before it answers, which takes minutes. "
            "Run `genesis ready` once and it starts at once.")
    if "library" not in started and program(vault, "kiwix", "kiwix-serve") and books(vault):
        missing.append("the book reader (this PC lacks Microsoft's Visual C++ runtime, which the reader needs. "
                       "It is free from Microsoft: %s  Install it and start Genesis again)" % REDIST)
    elif "library" not in started:
        missing.append("the book reader (no Kiwix server for this machine on the drive)")
    if "assistant" not in started:
        missing.append("the assistant (no Ollama for this machine, or no models, on the drive)")
    for what in missing:
        log("not started: " + what)
    port = int(os.environ.get("GENESIS_PORT", DASHBOARD_PORT))
    for _ in range(40):
        if _listening(port):
            break
        time.sleep(0.25)
    else:
        log("the dashboard did not come up: see %s" % os.path.join(_run_dir(), "dashboard.log"))
        return 1
    url = "http://localhost:%d/" % port
    log("Genesis is up: " + url)
    if not getattr(args, "no_browser", False) and (os.name == "nt" or sys.platform == "darwin"
                                                    or os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):
        if sys.platform.startswith("linux"):
            try:
                dress(vault)  # on the machine that shows them, the folder pictures get this machine's paths
            except OSError:
                pass
        webbrowser.open(url)
    return 0


def stop(args):
    path = os.path.join(_run_dir(), "box.json")
    for name, pid in sorted(read_json(path, {}).items()):
        try:
            os.kill(pid, signal.SIGTERM)
            log("stopped %s" % name)
        except (OSError, ValueError):
            pass
    write_json(path, {})
    return 0
