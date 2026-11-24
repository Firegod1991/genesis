"""Genesis command line. Run `genesis help` for the list."""

import argparse
import os
import subprocess
import sys

from . import __version__, catalog
from .util import ROOT, FetchError, human


def cmd_lint(args):
    problems = catalog.lint()
    for p in problems:
        print("PROBLEM " + p)
    print("%d packs, %d problems" % (len(catalog.pack_names()), len(problems)))
    return 1 if problems else 0


def cmd_refresh(args):
    from .refresh import refresh
    results = refresh(args.pack or None, jobs=args.jobs)
    failed = sum(len(r["failures"]) for r in results)
    total = sum(len(r["items"]) for r in results)
    print("resolved %d items across %d packs, %d lines failed" % (total, len(results), failed))
    if args.strict and failed:
        return 1
    # A refresh that resolves nothing at all means the network or the tool is broken.
    return 0 if total > failed else 1


def cmd_plan(args):
    from .fetch import plan
    return plan(args)


def cmd_fetch(args):
    from .fetch import fetch
    return fetch(args)


def cmd_verify(args):
    if args.sealed:
        from .seal import check
        return check(args)
    from .fetch import verify
    return verify(args)


def cmd_status(args):
    from .fetch import status
    return status(args)


def cmd_doctor(args):
    from .doctor import doctor
    return doctor(args)


def cmd_poi(args):
    from . import poi
    return poi.main(args)


def cmd_maps(args):
    from . import maps
    return maps.main(args)


def cmd_stack(args):
    from . import stack
    return stack.main(args)


def cmd_seal(args):
    from .seal import seal
    return seal(args)


def cmd_box(args):
    from . import box
    return {"ready": box.ready, "start": box.start, "stop": box.stop}[args.cmd](args)


def cmd_safe(args):
    from . import safe
    return safe.main(args)


def cmd_bootstick(args):
    from . import bootstick
    return bootstick.main(args)


def _selection(p):
    p.add_argument("--tier", choices=[catalog.STARTER] + catalog.TIERS,
                   help="seed, ark or eden (cumulative), or starter: a first gigabyte out of seed")
    p.add_argument("--pack", action="append", help="limit to a pack (repeatable)")
    p.add_argument("--id", action="append", help="fetch exact item ids (repeatable)")
    p.add_argument("--lang", help="comma separated ISO 639-3 codes, or * for every language")
    p.add_argument("--platform", help="comma separated: " + ", ".join(catalog.PLATFORMS)
                   + ". What has no build for them comes as its source")
    p.add_argument("--tag", action="append", help="only items carrying this tag (repeatable)")
    p.add_argument("--also", action="append", help="an item to keep on top of the tier, by id (repeatable, remembered)")
    p.add_argument("--vault", help="where content lives (default: $GENESIS_VAULT or ./vault)")


def build_parser():
    ap = argparse.ArgumentParser(prog="genesis", description="Genesis: everything you need to begin again.")
    ap.add_argument("--version", action="version", version="genesis " + __version__)
    sub = ap.add_subparsers(dest="cmd")

    sub.add_parser("lint", help="check the pack files").set_defaults(fn=cmd_lint)

    p = sub.add_parser("refresh", help="resolve the newest version of everything (what the monthly job runs)")
    p.add_argument("--pack", action="append")
    p.add_argument("--jobs", type=int, default=8)
    p.add_argument("--strict", action="store_true", help="exit non zero if any line fails")
    p.set_defaults(fn=cmd_refresh)

    p = sub.add_parser("plan", help="show what a tier would download and whether it fits")
    _selection(p)
    p.set_defaults(fn=cmd_plan)

    p = sub.add_parser("fetch", help="download and verify content into the vault")
    _selection(p)
    p.add_argument("--yes", action="store_true", help="do not ask before large downloads")
    p.add_argument("--keep-going", action="store_true", help="continue past failed items")
    p.add_argument("--jobs", type=int, default=4, help="downloads at once (default 4; 1 draws a progress bar per file)")
    p.set_defaults(fn=cmd_fetch)

    p = sub.add_parser("verify", help="re hash everything in the vault to catch bit rot")
    p.add_argument("--vault")
    p.add_argument("--sealed", action="store_true", help="check every file against the manifest `genesis seal` wrote")
    p.add_argument("--jobs", type=int, default=4, help="files checked at once (default 4)")
    p.set_defaults(fn=cmd_verify)

    p = sub.add_parser("status", help="what is installed, what is outdated")
    p.add_argument("--vault")
    p.set_defaults(fn=cmd_status)

    p = sub.add_parser("doctor", help="inspect this machine and recommend a tier")
    p.add_argument("--vault")
    p.add_argument("--json", action="store_true")
    p.set_defaults(fn=cmd_doctor)

    p = sub.add_parser("poi", help="survival layers: water, medical, fuel, power and more")
    p.add_argument("action", choices=["build", "merge", "nearest", "stats", "export", "categories"])
    p.add_argument("--source", action="append", help="a database to merge in (repeatable)")
    p.add_argument("--pbf", help="an OpenStreetMap .osm.pbf extract to read")
    p.add_argument("--region", help="a Geofabrik region id to download, such as europe/monaco or us/california")
    p.add_argument("--db", help="database path (default: <vault>/poi/survival.sqlite)")
    p.add_argument("--replace", action="store_true", help="start a fresh database instead of adding to it")
    p.add_argument("--lat", type=float)
    p.add_argument("--lon", type=float)
    p.add_argument("--cat", help="category, see `genesis poi categories`")
    p.add_argument("-n", type=int, default=5)
    p.add_argument("--format", choices=["csv", "geojson"], default="csv")
    p.add_argument("--out")
    p.add_argument("--vault")
    p.set_defaults(fn=cmd_poi)

    p = sub.add_parser("maps", help="offline vector maps (PMTiles)")
    p.add_argument("action", choices=["tool", "world", "region", "list"])
    p.add_argument("--name", help="output name for a region extract")
    p.add_argument("--bbox", help="west,south,east,north")
    p.add_argument("--region", help="a Geofabrik region id to cut, such as us/california")
    p.add_argument("--maxzoom", type=int)
    p.add_argument("--vault")
    p.set_defaults(fn=cmd_maps)

    for name, text in (("up", "start services"), ("down", "stop services"), ("ps", "list services"),
                       ("update", "pull the newest catalog, then fetch updates and restart"),
                       ("timer", "install the monthly update timer")):
        p = sub.add_parser(name, help=text)
        p.add_argument("profiles", nargs="*", help="compose profiles, default: core")
        p.add_argument("--vault")
        p.set_defaults(fn=cmd_stack, action=name)

    p = sub.add_parser("seal", help="write an offline restore kit (images, repo, checksums) into the vault")
    p.add_argument("--vault")
    p.add_argument("--quick", action="store_true",
                   help="read only what is new: a file whose size and time have not changed keeps its checksum")
    p.set_defaults(fn=cmd_seal)

    p = sub.add_parser("ready", help="unpack what the drive carries so it starts with no Docker and no internet")
    p.add_argument("--vault")
    p.set_defaults(fn=cmd_box)
    p = sub.add_parser("start", help="start Genesis from the drive with no Docker: dashboard, library, assistant")
    p.add_argument("--vault")
    p.add_argument("--no-browser", action="store_true", help="do not open the dashboard in a browser")
    p.add_argument("--no-ask", action="store_true", help="on a first start, do not offer to download anything")
    p.set_defaults(fn=cmd_box)
    p = sub.add_parser("stop", help="stop what `genesis start` started")
    p.add_argument("--vault")
    p.set_defaults(fn=cmd_box)

    p = sub.add_parser("safe", help="your own papers on the drive, locked behind a passphrase")
    p.add_argument("action", nargs="?", choices=["open", "lock", "status"],
                   help="with none: open the safe, or lock it when it is open")
    p.add_argument("--empty", action="store_true", help="with lock: close an emptied folder and keep the safe as it was")
    p.add_argument("--new-passphrase", action="store_true",
                   help="with lock: lock it under a new passphrase (the old one is asked for first)")
    p.add_argument("--vault")
    p.set_defaults(fn=cmd_safe)

    p = sub.add_parser("bootstick", help="make a USB stick that starts a PC, or a card that starts a Raspberry Pi")
    p.add_argument("--device", help="the stick or card to erase, such as /dev/sdb. With none, the candidates are listed")
    p.add_argument("--pi", action="store_true", help="write Raspberry Pi OS to a card instead of making a stick for a PC")
    p.add_argument("--lite", action="store_true", help="with --pi: the system without a desktop")
    p.add_argument("--iso", action="append", help="a system to put on the stick, by part of its file name (repeatable)")
    p.add_argument("--yes", action="store_true", help="do not ask before erasing")
    p.add_argument("--force", action="store_true", help="allow a disk that does not look like a stick or a card")
    p.add_argument("--vault")
    p.set_defaults(fn=cmd_bootstick)
    return ap


def vault_beside():
    """On a drive the vault sits beside this folder, not inside it. Use that one when nothing else is said,
    so the drive starts the same way from every system's launcher and from `python -m genesis`."""
    beside = os.path.join(os.path.dirname(ROOT), "vault")
    if (not os.environ.get("GENESIS_VAULT") and not os.path.isdir(os.path.join(ROOT, "vault"))
            and os.path.isdir(os.path.join(beside, ".genesis"))):
        os.environ["GENESIS_VAULT"] = beside
        return beside
    return None


def main(argv=None):
    ap = build_parser()
    args = ap.parse_args(argv)
    vault_beside()
    if not getattr(args, "fn", None):
        ap.print_help()
        return 0
    try:
        return args.fn(args) or 0
    except KeyboardInterrupt:
        print("interrupted", file=sys.stderr)
        return 130
    except FetchError as e:
        # A source that is down or turns the connection away is not a bug: one line, no traceback.
        print("genesis: %s" % e, file=sys.stderr)
        return 1
    except subprocess.CalledProcessError as e:
        tool = e.cmd[0] if isinstance(e.cmd, (list, tuple)) else str(e.cmd).split()[0]
        print("genesis: %s stopped with exit code %s" % (tool, e.returncode), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())

_ = human  # re exported for convenience in interactive use
