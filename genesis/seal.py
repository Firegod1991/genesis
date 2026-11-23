"""Seal: make the vault able to rebuild a Genesis box with no internet at all.

Writes <vault>/restore with the container images, a copy of this repository,
and a manifest of every file with its SHA 256 so the drive can prove itself.
"""

import datetime
import os
import shutil
import subprocess

from . import __version__, catalog
from .util import ROOT, file_hash, human, log, read_json, write_json

RESTORE = """Genesis restore kit
===================

Sealed %(date)s with Genesis %(version)s. Everything here works with no network.

1. Install Docker on the new machine (carry its packages on this drive if you can).
2. Load the services:      for f in images/*.tar; do docker load -i "$f"; done
3. Restore the code:       git clone genesis.bundle genesis
4. Point at this drive:    export GENESIS_VAULT=<path to this drive>
5. Start:                  cd genesis && sh bin/genesis up core

MANIFEST.json lists every file in the vault with its size and SHA 256.
Check the whole drive against it any time with: sh bin/genesis verify --sealed
"""


def _recorded(vault):
    """The checksums a fetch kept for what it downloaded and proved, by path inside the vault."""
    out = {}
    for st in (read_json(os.path.join(vault, ".genesis", "state.json"), {}) or {}).values():
        out.update(st.get("sums") or {})
    return out


def _unchanged(rel, size, when, exact, known, sealed_at, fetched):
    """The checksum already known for a file that has not changed since it was last read through, or None.

    A file counts as unchanged when its size and its time are what the last seal wrote down. A seal made
    before times were written down vouches for every file not touched since that seal. A download keeps the
    checksum it was proved with for as long as its size and time stay as they were then.
    """
    was = known.get(rel)
    if was and was.get("bytes") == size and (was["mtime"] == when if "mtime" in was else exact <= sealed_at):
        return was["sha256"]
    got = fetched.get(rel)
    if got and got.get("bytes") == size and got.get("mtime") == when:
        return got["sha256"]
    return None


def seal(args):
    from .fetch import vault_path
    vault = vault_path(args)
    out = os.path.join(vault, "restore")
    quick, last = getattr(args, "quick", False), os.path.join(out, "MANIFEST.json")
    known = (read_json(last, {}) or {}).get("files", {}) if quick else {}
    sealed_at = os.path.getmtime(last) if quick and os.path.isfile(last) else 0
    fetched = _recorded(vault) if quick else {}
    os.makedirs(os.path.join(out, "images"), exist_ok=True)

    saved = 0
    if shutil.which("docker"):
        for _, iid, entry in catalog.all_locked(["services"]):
            image = entry.get("image")
            if not image or subprocess.call(["docker", "image", "inspect", image],
                                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL) != 0:
                continue  # only what this box actually runs
            target = os.path.join(out, "images", iid + ".tar")
            log("saving %s" % image)
            if subprocess.call(["docker", "save", "-o", target, image]) == 0:
                saved += 1
    else:
        log("docker not found: skipping images")

    bundle = os.path.join(out, "genesis.bundle")
    bundled = (os.path.isdir(os.path.join(ROOT, ".git")) and shutil.which("git") and subprocess.call(
        ["git", "-C", ROOT, "bundle", "create", bundle, "--all"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL) == 0)
    if not bundled:  # not a git checkout: carry a plain archive of the code instead
        shutil.make_archive(os.path.join(out, "genesis"), "gztar", ROOT,
                            base_dir=".", logger=None)

    manifest, total, kept, read = {}, 0, 0, 0
    for folder, dirs, files in os.walk(vault):
        # Left out: the records Genesis keeps for itself, and what people keep on the drive themselves
        # (their notes, their safe), which changes whenever they use it.
        dirs[:] = [d for d in dirs if not (folder == vault and d in (".genesis", "files"))]
        for name in files:
            path = os.path.join(folder, name)
            rel = os.path.relpath(path, vault)
            if rel == os.path.join("restore", "MANIFEST.json") or name.endswith((".part", ".part.aria2")) or name == ".directory":
                continue  # the manifest itself, downloads still under way, and folder pictures each machine rewrites
            st = os.stat(path)
            key, size, when = rel.replace(os.sep, "/"), st.st_size, int(st.st_mtime)
            digest = _unchanged(key, size, when, st.st_mtime, known, sealed_at, fetched) if quick else None
            if digest:
                kept += 1
            else:
                digest = file_hash(path)
                read += size
            manifest[key] = {"bytes": size, "mtime": when, "sha256": digest}
            total += size
    today = datetime.date.today().isoformat()
    write_json(os.path.join(out, "MANIFEST.json"), {"sealed": today, "genesis": __version__, "files": manifest})
    with open(os.path.join(out, "RESTORE.txt"), "w", encoding="utf-8") as f:
        f.write(RESTORE % {"date": today, "version": __version__})
    log("sealed %d files (%s) and %d service images into %s" % (len(manifest), human(total), saved, out))
    if quick:
        log("read through %s. %d files had not changed since they were last read through and kept their checksum."
            % (human(read), kept))
    return 0


def check(args):
    """Prove the drive against its seal: every file the manifest lists, by size and by SHA 256."""
    from .fetch import vault_path
    vault = vault_path(args)
    manifest = read_json(os.path.join(vault, "restore", "MANIFEST.json"))
    if not manifest:
        print("this vault has no seal yet: run `genesis seal` first")
        return 1
    bad, total = [], 0
    for rel, want in sorted(manifest["files"].items()):
        path = os.path.join(vault, *rel.split("/"))
        if not os.path.isfile(path):
            bad.append("missing: %s" % rel)
        elif os.path.getsize(path) != want["bytes"]:
            bad.append("size changed: %s" % rel)
        elif file_hash(path) != want["sha256"]:
            bad.append("contents changed: %s" % rel)
        total += want["bytes"]
    for b in bad:
        print("BAD " + b)
    print("sealed %s: %d files (%s) checked against the manifest, %d problems"
          % (manifest.get("sealed"), len(manifest["files"]), human(total), len(bad)))
    return 1 if bad else 0
