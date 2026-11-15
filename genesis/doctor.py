"""Inspect this machine and say what it can carry."""

import json
import os
import platform
import shutil
import subprocess

from . import catalog
from .util import human, read_json

# USB ids of common receivers, so we can tell the owner what the radio stack will see.
SDR_IDS = {"0bda:2838": "RTL-SDR", "0bda:2832": "RTL-SDR", "1d50:6089": "HackRF One", "1d50:60a1": "Airspy",
           "03eb:800c": "Airspy HF+", "1df7:2500": "SDRplay RSP1", "1df7:3000": "SDRplay RSP1A",
           "1d50:6108": "LimeSDR", "0403:601f": "LimeSDR Mini", "2500:0020": "USRP B200"}


def _run(cmd):
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=15).stdout
    except (OSError, subprocess.SubprocessError):
        return ""


def _mem_gb():
    try:
        with open("/proc/meminfo") as f:
            for line in f:
                if line.startswith("MemTotal"):
                    return int(line.split()[1]) / 1e6
    except OSError:
        pass
    return None


def inspect(vault):
    info = {"os": platform.platform(), "platform": catalog.host_platform(), "cpus": os.cpu_count(),
            "ram_gb": _mem_gb(), "vault": vault}
    probe = vault
    while not os.path.exists(probe):
        probe = os.path.dirname(probe)
    info["free_bytes"] = shutil.disk_usage(probe).free
    try:
        with open("/proc/device-tree/model") as f:
            info["board"] = f.read().strip("\x00\n")
    except OSError:
        pass
    gpus = []
    for line in _run(["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader,nounits"]).splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) == 2 and parts[1].isdigit():
            gpus.append({"name": parts[0], "vram_gb": int(parts[1]) / 1024.0})
    info["gpus"] = gpus
    info["tools"] = {t: bool(shutil.which(t)) for t in ("docker", "curl", "git", "osmium")}
    info["compose"] = bool(_run(["docker", "compose", "version"]).strip())
    usb = _run(["lsusb"])
    info["sdr"] = sorted({name for usbid, name in SDR_IDS.items() if usbid in usb.lower()})
    info["serial"] = sorted(d for d in os.listdir("/dev") if d.startswith(("ttyUSB", "ttyACM"))) if os.path.isdir("/dev") else []
    vram = max([g["vram_gb"] for g in gpus] or [0])
    ram = info["ram_gb"] or 0
    info["ai_tier"] = "eden" if vram >= 20 else "ark" if vram >= 7.5 else "seed" if ram >= 7 else None
    totals = read_json(os.path.join(catalog.LOCK_DIR, "_meta.json"), {}).get("tiers", {})
    fits = [t for t in catalog.TIERS if totals.get(t) and totals[t]["bytes"] * 1.1 < info["free_bytes"]]
    info["content_tier"] = fits[-1] if fits else None
    info["tier_bytes"] = {t: totals[t]["bytes"] for t in totals}
    return info


def doctor(args):
    from .fetch import vault_path
    info = inspect(vault_path(args))
    if args.json:
        print(json.dumps(info, indent=1))
        return 0
    print("machine   %s (%s), %s CPUs, %s RAM" % (
        info.get("board") or info["os"], info["platform"], info["cpus"],
        ("%.0f GB" % info["ram_gb"]) if info["ram_gb"] else "unknown"))
    for g in info["gpus"]:
        print("gpu       %s, %.0f GB" % (g["name"], g["vram_gb"]))
    if not info["gpus"]:
        print("gpu       none found (AI runs on CPU: small models only)")
    print("vault     %s, %s free" % (info["vault"], human(info["free_bytes"])))
    for tier, size in info["tier_bytes"].items():
        print("          %-5s needs about %s" % (tier, human(size)))
    print("radio     %s" % (", ".join(info["sdr"]) or "no SDR receiver plugged in"))
    if info["serial"]:
        print("serial    %s (mesh radios show up here)" % ", ".join(info["serial"]))
    missing = [t for t, ok in info["tools"].items() if not ok] + ([] if info["compose"] else ["docker compose"])
    print("tools     %s" % ("all present" if not missing else "missing: " + ", ".join(missing)))
    print()
    print("content tier that fits this drive: %s" % (info["content_tier"] or "none yet: free up space or use a bigger drive"))
    print("AI tier this machine can run:      %s" % (info["ai_tier"] or "none: under 8 GB of RAM"))
    if info["content_tier"]:
        print("\nnext: genesis fetch --tier %s" % info["content_tier"])
    return 0
