"""Seal: make the vault able to rebuild a Genesis box with no internet at all.

Writes <vault>/restore with the container images, a copy of this repository,
and a manifest of every file with its SHA 256 so the drive can prove itself.
"""

import datetime
import os
import shutil
import subprocess

from . import __version__, catalog
from .util import ROOT, file_hash, human, log, write_json

RESTORE = """Genesis restore kit
===================

Sealed %(date)s with Genesis %(version)s. Everything here works with no network.

1. Install Docker on the new machine (carry its packages on this drive if you can).
2. Load the services:      for f in images/*.tar; do docker load -i "$f"; done
3. Restore the code:       git clone genesis.bundle genesis
4. Point at this drive:    export GENESIS_VAULT=<path to this drive>
5. Start:                  cd genesis && sh bin/genesis up core

MANIFEST.json lists every file in the vault with its size and SHA 256.
Check the drive any time with: sh bin/genesis verify
"""


def seal(args):
    from .fetch import vault_path
    vault = vault_path(args)
    out = os.path.join(vault, "restore")
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

    manifest, total = {}, 0
    for folder, dirs, files in os.walk(vault):
        dirs[:] = [d for d in dirs if not (folder == vault and d in (".genesis",))]
        for name in files:
            path = os.path.join(folder, name)
            rel = os.path.relpath(path, vault)
            if rel == os.path.join("restore", "MANIFEST.json") or name.endswith(".part"):
                continue
            size = os.path.getsize(path)
            manifest[rel] = {"bytes": size, "sha256": file_hash(path)}
            total += size
    today = datetime.date.today().isoformat()
    write_json(os.path.join(out, "MANIFEST.json"), {"sealed": today, "genesis": __version__, "files": manifest})
    with open(os.path.join(out, "RESTORE.txt"), "w", encoding="utf-8") as f:
        f.write(RESTORE % {"date": today, "version": __version__})
    log("sealed %d files (%s) and %d service images into %s" % (len(manifest), human(total), saved, out))
    return 0
