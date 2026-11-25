#!/usr/bin/env python3
"""Build the downloads a person who has never used a terminal can start with a double click.

    python3 tools/package.py [--out dist]

Writes three archives, each with one folder named Genesis inside:

  Genesis-Windows.zip     Start Genesis.cmd, and Python itself in a python folder, so nothing needs installing
  Genesis-Mac.zip         Start Genesis.command, run with the Python that Apple's own tools bring
  Genesis-Linux.tar.gz    Start Genesis.desktop, run with the Python every Linux and Raspberry Pi OS has

and a SHA256SUMS file. Each carries what `git ls-files` lists, less what only building Genesis needs, with
the program files marked executable whatever system builds them. Python for Windows is the embeddable
package from python.org, checked against the SHA 256 written here before it goes in.
"""

import hashlib
import io
import os
import subprocess
import sys
import tarfile
import time
import urllib.request
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PYTHON = "3.14.10"
PYTHON_URL = "https://www.python.org/ftp/python/%s/python-%s-embed-amd64.zip" % (PYTHON, PYTHON)
PYTHON_SHA256 = "90905e64a840557d8c1bc4f5b74db3d7ee85e3ffb217611cac1c7813acea4159"
LEFT_OUT = (".github/", "reports/", "tests/", ".gitattributes", ".gitignore")
EXECUTABLE = ("bin/genesis", "bin/genesis-desktop", "install.sh", "dashboard/server.py",
              "Start Genesis.command", "Stop Genesis.command", "Start Genesis.desktop", "Stop Genesis.desktop")
ONLY = {  # files that belong to one system's download
    "windows": (".cmd",),
    "mac": (".command",),
    "linux": (".desktop", "install.sh", "bin/genesis-desktop"),
}
FIRST = {
    "windows": """Genesis for Windows

1. Double click Start Genesis.
2. The first time, choose what to bring down. Press Enter for the starter and the assistant.
3. Genesis opens in your browser. It keeps running after the window closes.

Windows may ask whether to let Python through its firewall. Yes lets the phones on your network open
Genesis: the home page shows a code to point a phone's camera at. No keeps it to this PC.

If Windows says it protected your PC, choose More info, then Run anyway.
To stop Genesis, double click Stop Genesis. Everything it brings down stays in the vault folder here.
Nothing needs installing: Python is in the python folder.
""",
    "mac": """Genesis for a Mac

1. Right click Start Genesis.command and choose Open, then Open again. After the first time a
   double click is enough. On macOS 15 and newer, if there is no Open button: open System Settings,
   Privacy and Security, scroll down and choose Open Anyway.
2. If your Mac offers to install the command line developer tools, say Install: they bring Python.
   Then open Start Genesis again.
3. The first time, choose what to bring down. Press Enter for the starter and the assistant.
4. Genesis opens in your browser. The home page shows a code to point a phone's camera at.

To stop Genesis, open Stop Genesis.command. Everything it brings down stays in the vault folder here.
""",
    "linux": """Genesis for Linux and the Raspberry Pi

1. Double click Start Genesis. If the desktop asks, choose Allow Launching, Trust or Execute.
   Or, in a terminal in this folder:   sh bin/genesis start
2. The first time, choose what to bring down. Press Enter for the starter and the assistant.
3. Genesis opens in your browser. The home page shows a code to point a phone's camera at.

To stop Genesis, double click Stop Genesis, or run: sh bin/genesis stop
Everything it brings down stays in the vault folder here. Python 3.9 or newer is all it needs.
For the full station with Docker, see README.md.
""",
}


def tracked():
    out = subprocess.run(["git", "ls-files", "-z"], cwd=ROOT, capture_output=True, check=True).stdout
    return sorted(p for p in out.decode().split("\0") if p and not p.startswith(LEFT_OUT) and p not in LEFT_OUT)


def wanted(path, system):
    for other, marks in ONLY.items():
        if other != system and any(path == m or path.endswith(m) for m in marks):
            return False
    return True


def content(path):
    with open(os.path.join(ROOT, path), "rb") as f:
        data = f.read()
    if path.endswith(".cmd"):  # the Windows command processor wants its own line endings
        data = data.replace(b"\r\n", b"\n").replace(b"\n", b"\r\n")
    return data


def python_for_windows(cache):
    path = os.path.join(cache, os.path.basename(PYTHON_URL))
    if not os.path.isfile(path):
        os.makedirs(cache, exist_ok=True)
        with urllib.request.urlopen(PYTHON_URL, timeout=120) as r, open(path + ".part", "wb") as f:
            f.write(r.read())
        os.replace(path + ".part", path)
    with open(path, "rb") as f:
        got = hashlib.sha256(f.read()).hexdigest()
    if got != PYTHON_SHA256:
        raise SystemExit("python.org gave %s with SHA 256 %s, not %s" % (os.path.basename(path), got, PYTHON_SHA256))
    return path


def entries(system, cache):
    """(name inside the folder, bytes, executable) for one system's download."""
    out = [(p, content(p), p in EXECUTABLE) for p in tracked() if wanted(p, system)]
    out.append(("HOW TO START.txt", FIRST[system].replace("\n", "\r\n" if system == "windows" else "\n").encode(), False))
    if system == "windows":
        with zipfile.ZipFile(python_for_windows(cache)) as z:
            for info in z.infolist():
                data = z.read(info)
                if info.filename.endswith("._pth"):
                    # Genesis lives one folder up from Python: let `import genesis` find it there.
                    data = data.replace(b"\r\n", b"\n").replace(b"\n.\n", b"\n.\n..\n").replace(b"\n", b"\r\n")
                    if b"\r\n..\r\n" not in data:
                        raise SystemExit("the ._pth file of Python for Windows has changed shape: %r" % data)
                out.append(("python/" + info.filename, data, False))
    return out


def write_zip(path, items, stamp):
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for name, data, exe in items:
            info = zipfile.ZipInfo("Genesis/" + name, date_time=time.gmtime(stamp)[:6])
            info.create_system = 3  # Unix, so the Mac's Archive Utility keeps the executable bit
            info.external_attr = ((0o755 if exe else 0o644) | 0o100000) << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            z.writestr(info, data)


def write_tar(path, items, stamp):
    with tarfile.open(path, "w:gz", format=tarfile.PAX_FORMAT) as t:
        for name, data, exe in items:
            info = tarfile.TarInfo("Genesis/" + name)
            info.size, info.mtime, info.mode = len(data), stamp, 0o755 if exe else 0o644
            t.addfile(info, io.BytesIO(data))


def main(argv):
    out = os.path.join(ROOT, "dist")
    if "--out" in argv:
        out = os.path.abspath(argv[argv.index("--out") + 1])
    os.makedirs(out, exist_ok=True)
    stamp = int(subprocess.run(["git", "log", "-1", "--format=%ct"], cwd=ROOT, capture_output=True, text=True).stdout.strip() or time.time())
    cache = os.path.join(out, ".cache")
    made = {}
    for system, name in (("windows", "Genesis-Windows.zip"), ("mac", "Genesis-Mac.zip"), ("linux", "Genesis-Linux.tar.gz")):
        items = entries(system, cache)
        path = os.path.join(out, name)
        (write_tar if name.endswith(".tar.gz") else write_zip)(path, items, stamp)
        with open(path, "rb") as f:
            made[name] = hashlib.sha256(f.read()).hexdigest()
        print("%-22s %5.1f MB  %d files" % (name, os.path.getsize(path) / 1e6, len(items)))
    with open(os.path.join(out, "SHA256SUMS"), "w") as f:
        f.writelines("%s  %s\n" % (h, n) for n, h in sorted(made.items()))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
