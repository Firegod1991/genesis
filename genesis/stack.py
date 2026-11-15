"""Service stack: a thin wrapper around `docker compose`, plus the monthly update."""

import os
import shutil
import subprocess
import sys

from .util import ROOT, log, read_json, write_json

UNIT = """[Unit]
Description=Genesis monthly update
Wants=network-online.target
After=network-online.target

[Service]
Type=oneshot
Environment=GENESIS_VAULT={vault}
ExecStart={python} -m genesis update
WorkingDirectory={root}
"""
TIMER = """[Unit]
Description=Run the Genesis update once a month

[Timer]
OnCalendar=monthly
RandomizedDelaySec=6h
Persistent=true

[Install]
WantedBy=timers.target
"""


def _gpu():
    return bool(shutil.which("nvidia-smi")) and "nvidia" in subprocess.run(
        ["docker", "info", "--format", "{{json .Runtimes}}"], capture_output=True, text=True).stdout.lower()


def compose(vault, profiles, *cmd):
    env = dict(os.environ, GENESIS_VAULT=vault, GENESIS_ROOT=ROOT)
    # Windows has no user ids. Docker Desktop maps the files there, and 1000 is the images' own user.
    env.setdefault("GENESIS_UID", str(getattr(os, "getuid", lambda: 1000)()))
    env.setdefault("GENESIS_GID", str(getattr(os, "getgid", lambda: 1000)()))
    base = ["docker", "compose", "--project-name", "genesis", "-f", os.path.join(ROOT, "compose.yaml")]
    if "ai" in profiles and _gpu():
        base += ["-f", os.path.join(ROOT, "compose.gpu.yaml")]
    for p in profiles:
        base += ["--profile", p]
    return subprocess.call(base + list(cmd), env=env)


def _timer(vault):
    if not hasattr(os, "geteuid"):
        log("the monthly timer uses systemd. On Windows, run `python -m genesis update` from Task Scheduler.")
        return 1
    system = os.geteuid() == 0
    folder = "/etc/systemd/system" if system else os.path.expanduser("~/.config/systemd/user")
    os.makedirs(folder, exist_ok=True)
    with open(os.path.join(folder, "genesis-update.service"), "w") as f:
        f.write(UNIT.format(vault=vault, python=sys.executable, root=ROOT))
    with open(os.path.join(folder, "genesis-update.timer"), "w") as f:
        f.write(TIMER)
    ctl = ["systemctl"] + ([] if system else ["--user"])
    if not shutil.which("systemctl"):
        log("wrote the units to %s but systemctl is missing: run `genesis update` from cron instead" % folder)
        return 1
    subprocess.call(ctl + ["daemon-reload"])
    rc = subprocess.call(ctl + ["enable", "--now", "genesis-update.timer"])
    log("monthly update timer installed" if rc == 0 else "could not enable the timer")
    return rc


def _update(args, vault, profiles):
    """Newest catalog from GitHub, then whatever changed, then restart so Kiwix sees new books."""
    if os.path.isdir(os.path.join(ROOT, ".git")) and shutil.which("git"):
        rc = subprocess.call(["git", "-C", ROOT, "pull", "--ff-only", "--quiet"])
        log("catalog is current" if rc == 0 else "could not reach GitHub: using the catalog already on disk")
    from .fetch import fetch
    args.tier = args.lang = args.platform = args.pack = args.id = args.tag = None
    args.yes, args.keep_going = True, True
    rc = fetch(args)
    if shutil.which("docker"):
        compose(vault, profiles, "pull", "--quiet")
        compose(vault, profiles, "up", "-d", "--remove-orphans")
        compose(vault, profiles, "restart", "kiwix")
    return rc


def main(args):
    from .fetch import _meta, vault_path
    vault = vault_path(args)
    os.makedirs(os.path.join(vault, ".genesis"), exist_ok=True)
    saved = read_json(_meta(vault, "profiles.json"), ["core"])
    profiles = args.profiles or saved
    if args.action == "timer":
        return _timer(vault)
    if args.action == "update":
        return _update(args, vault, profiles)
    if not shutil.which("docker"):
        raise SystemExit("docker is not installed: run ./install.sh or see https://docs.docker.com/engine/install/")
    if args.action == "up":
        # Made here, as you. A folder Docker has to make for a service belongs to root instead,
        # and then neither you nor the file browser can write to it.
        for sub in ("zim", "maps", "scripture", "poi", "apps", "config",
                    "files", "files/notes", "files/books", "files/media", "files/code"):
            os.makedirs(os.path.join(vault, *sub.split("/")), exist_ok=True)
        write_json(_meta(vault, "profiles.json"), sorted(set(profiles)))
        rc = compose(vault, profiles, "up", "-d", "--remove-orphans")
        if rc == 0:
            log("Genesis is up: http://localhost:%s" % os.environ.get("GENESIS_PORT", "8080"))
        return rc
    if args.action == "down":
        return compose(vault, saved, "down")
    return compose(vault, saved, "ps")
