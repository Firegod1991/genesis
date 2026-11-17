"""The box with no Docker: the dashboard, the Kiwix reader and the local models, started straight
from what is on the drive.

A Raspberry Pi with nothing but the drive, a screen and a keyboard runs this. `genesis ready`
is done once, while there is still a connection or another machine to do it on: it unpacks the
programs the drive carries, lists the library and draws the covers. `genesis start` needs
nothing but Python and the drive.
"""

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
import webbrowser

from . import catalog
from .util import ROOT, log, read_json, write_json

DASHBOARD_PORT, KIWIX_PORT, OLLAMA_PORT = 8080, 8081, 11434
# How the archives in <vault>/tools name each machine, and what Genesis calls it.
KIWIX = re.compile(r"^kiwix-tools_(linux-aarch64|linux-x86_64)-[\w.]+\.tar\.gz$")
OLLAMA = re.compile(r"^ollama-linux-(arm64|amd64)\.tar\.zst$")
MACHINE = {"linux-aarch64": "linux-arm64", "linux-x86_64": "linux-x64", "arm64": "linux-arm64", "amd64": "linux-x64"}

DESKTOP = """[Desktop Entry]
Type=Application
Name=Start Genesis
Comment=Maps, manuals, the library and the assistant, with no internet
Exec=sh -c 'exec "$(dirname "%%k")/genesis/bin/genesis" start'
Icon=%s
Terminal=false
Categories=Education;
"""
START = """#!/bin/sh
# Start Genesis from this drive. Needs nothing but the drive and Python 3, which Raspberry Pi OS has.
exec "$(dirname "$0")/genesis/bin/genesis" start "$@"
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
    """Unpack the Kiwix server and Ollama the drive carries, for every machine it carries them for."""
    tools, done = os.path.join(vault, "tools"), []
    for name in sorted(os.listdir(tools)) if os.path.isdir(tools) else []:
        path = os.path.join(tools, name)
        kiwix, ollama = KIWIX.match(name), OLLAMA.match(name)
        if kiwix:
            into = os.path.join(runtime(vault, MACHINE[kiwix.group(1)]), "kiwix")
            if not _fresh(into, path):
                shutil.rmtree(into, ignore_errors=True)
                os.makedirs(into)
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
    return done


def program(vault, kind, name):
    """A program for this machine: the one unpacked from the drive, or one already installed."""
    exe = name + (".exe" if os.name == "nt" else "")
    for folder in (os.path.join(runtime(vault), kind), os.path.join(runtime(vault), kind, "bin")):
        if os.path.isfile(os.path.join(folder, exe)):
            return os.path.join(folder, exe)
    return shutil.which(name)


def books(vault):
    folder = os.path.join(vault, "zim")
    return sorted(os.path.join(folder, n) for n in os.listdir(folder) if n.endswith(".zim")) if os.path.isdir(folder) else []


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
    """Give the drive a front door and its folders their pictures. File managers on the Raspberry Pi
    read a folder's picture from a `.directory` file inside it, and want the full path."""
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
        for name in ("Start Genesis.sh", "Start Genesis.desktop"):
            os.chmod(os.path.join(root, name), 0o755)
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
    covers = draw_covers(vault)
    log("covers: pdftoppm is not installed, so the shelves show titles" if covers is None else "covers drawn: %d new" % covers)
    log("folder pictures: %d" % dress(vault))
    have = [m for m in sorted(set(MACHINE.values())) if os.path.isfile(os.path.join(runtime(vault, m), "kiwix", "kiwix-serve"))]
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


def _spawn(cmd, env, logname):
    out = open(os.path.join(_run_dir(), logname), "ab")
    kw = {"creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0)} if os.name == "nt" else {"start_new_session": True}
    return subprocess.Popen(cmd, env=env, stdin=subprocess.DEVNULL, stdout=out, stderr=subprocess.STDOUT, **kw).pid


def plan(vault):
    """What to start, as (name, port, command, extra environment). Only what this machine can run."""
    out = [("dashboard", int(os.environ.get("GENESIS_PORT", DASHBOARD_PORT)),
            [sys.executable, os.path.join(ROOT, "dashboard", "server.py")], {})]
    kiwix, shelf = program(vault, "kiwix", "kiwix-serve"), books(vault)
    if kiwix and shelf:
        out.append(("library", KIWIX_PORT, [kiwix, "--port", str(KIWIX_PORT)] + shelf, {}))
    ollama, models = program(vault, "ollama", "ollama"), os.path.join(vault, "models", "ollama", "models")
    if ollama and os.path.isdir(models):
        out.append(("assistant", OLLAMA_PORT, [ollama, "serve"],
                    {"OLLAMA_MODELS": models, "OLLAMA_HOST": "127.0.0.1:%d" % OLLAMA_PORT}))
    return out


def start(args):
    from .fetch import vault_path
    vault = vault_path(args)
    if not os.path.isdir(vault):
        raise SystemExit("no vault at %s: set GENESIS_VAULT to the drive's vault folder" % vault)
    if not program(vault, "kiwix", "kiwix-serve") and os.path.isdir(os.path.join(vault, "tools")):
        try:  # a drive filled without `genesis ready`: the Kiwix server unpacks with nothing but Python
            unpack_programs(vault)
        except OSError as e:
            log("could not unpack the programs on the drive: %s" % e)
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
    if "library" not in started:
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
