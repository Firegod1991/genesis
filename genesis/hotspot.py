"""The box's own WiFi: `genesis hotspot` turns a Raspberry Pi or a Linux laptop into a network of its own, so
phones can reach Genesis where there is no router and no internet.

It asks NetworkManager for the hotspot, which Raspberry Pi OS has used since Bookworm. A phone that joins is
sent straight to Genesis: every name it looks up leads to the box, and the box answers on the web port too,
so the phone's own check for a sign in page opens Genesis. That part needs the administrator's password
once, and `--no-portal` leaves it out. The box's WiFi is busy being the hotspot while it runs, so the box
leaves the WiFi it was on until `genesis hotspot stop`.
"""

import os
import random
import shutil
import subprocess
import sys
import time

from .util import log, read_json, write_json

CONNECTION = "genesis-hotspot"
PORTAL_CONF = "/etc/NetworkManager/dnsmasq-shared.d/genesis-portal.conf"
TABLE = "genesis_portal"
WORDS = ("amber", "anchor", "aspen", "basin", "beacon", "birch", "bramble", "canyon", "cedar", "clover", "comet",
         "copper", "creek", "delta", "ember", "falcon", "fern", "field", "flint", "forest", "garnet", "glacier",
         "granite", "harbor", "hazel", "heron", "island", "juniper", "kestrel", "lantern", "maple", "meadow",
         "mesa", "orchard", "otter", "pebble", "pine", "prairie", "quartz", "raven", "ridge", "river", "saddle",
         "sage", "spruce", "summit", "thistle", "timber", "valley", "willow")


def system():
    """Which system this is, as sys.platform names it: a function, so the tests can stand in another."""
    return sys.platform


def _settings_path(vault):
    return os.path.join(vault, ".genesis", "hotspot.json")


def _marker():
    from .box import _run_dir
    return os.path.join(_run_dir(), "hotspot.json")


def active():
    """The hotspot this box runs right now, as `genesis hotspot` left it, or None."""
    return read_json(_marker())


def join_code(name, password):
    """What a phone camera reads to join a WiFi network: the standard text inside a WiFi QR code."""
    def esc(text):
        return "".join("\\" + c if c in '\\;,:"' else c for c in text)
    return "WIFI:T:WPA;S:%s;P:%s;;" % (esc(name), esc(password))


def settings(vault, name=None, password=None):
    """The hotspot's name and password, kept on the drive so phones that joined once join again."""
    kept = read_json(_settings_path(vault), {}) or {}
    name = name or kept.get("name") or "Genesis"
    password = password or kept.get("password")
    if not password:
        pick = random.SystemRandom()
        password = "%s-%s-%d" % (pick.choice(WORDS), pick.choice(WORDS), pick.randrange(10, 100))
    if not 8 <= len(password) <= 63:
        raise SystemExit("a WiFi password is 8 to 63 characters long")
    if not 1 <= len(name.encode("utf-8")) <= 32:
        raise SystemExit("a WiFi name is 1 to 32 bytes long")
    write_json(_settings_path(vault), {"name": name, "password": password})
    return name, password


def _run(cmd, root=False, data=None):
    """Run a command, as the administrator when asked to. Returns (exit code, what it said)."""
    if root and hasattr(os, "geteuid") and os.geteuid() != 0:
        if not shutil.which("sudo"):
            return 1, "sudo is not installed"
        cmd = ["sudo"] + cmd
    try:
        done = subprocess.run(cmd, input=data, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, timeout=90)
    except (OSError, subprocess.TimeoutExpired) as e:
        return 1, str(e)
    return done.returncode, done.stdout.strip()


def wifi_device(nmcli):
    code, out = _run([nmcli, "-t", "-f", "DEVICE,TYPE", "device"])
    if code != 0:
        return None
    for line in out.splitlines():
        dev, _, kind = line.rpartition(":")
        if kind == "wifi" and dev and not dev.startswith("p2p"):
            return dev
    return None


def _address(nmcli, device):
    for _ in range(20):
        code, out = _run([nmcli, "-g", "IP4.ADDRESS", "device", "show", device])
        first = out.splitlines()[0].split("/")[0].strip() if code == 0 and out.strip() else ""
        if first:
            return first
        time.sleep(0.5)
    return None


def dashboard_port():
    """The port the dashboard answers on: the usual one, or the spare it moved to."""
    from .box import DASHBOARD_PORT, SPARE_PORTS, _ours
    for port in (int(os.environ.get("GENESIS_PORT", DASHBOARD_PORT)),) + SPARE_PORTS:
        if _ours(port):
            return port
    return int(os.environ.get("GENESIS_PORT", DASHBOARD_PORT))


def _portal_on(nmcli, device, address, port):
    """Lead every phone that joins to Genesis. Returns what could not be done, or None."""
    code, out = _run(["tee", PORTAL_CONF], root=True,
                     data="# Written by genesis hotspot: every name a phone on the hotspot looks up leads to this box.\n"
                          "address=/#/%s\n" % address)
    if code != 0:
        return "could not write %s (%s)" % (PORTAL_CONF, out[-200:])
    _run([nmcli, "connection", "up", CONNECTION])  # the hotspot's name server starts again and reads it
    nft, ipt = shutil.which("nft") or "/usr/sbin/nft", shutil.which("iptables") or "/usr/sbin/iptables"
    if os.path.exists(nft):
        _run([nft, "delete", "table", "ip", TABLE], root=True)
        rules = ("table ip %s {\n chain pre {\n  type nat hook prerouting priority dstnat;\n"
                 "  iifname \"%s\" tcp dport 80 redirect to :%d\n }\n}\n" % (TABLE, device, port))
        code, out = _run([nft, "-f", "-"], root=True, data=rules)
    elif os.path.exists(ipt):
        code, out = _run([ipt, "-t", "nat", "-I", "PREROUTING", "-i", device, "-p", "tcp", "--dport", "80",
                          "-j", "REDIRECT", "--to-ports", str(port)], root=True)
    else:
        return "neither nft nor iptables is installed, so phones are not sent to Genesis by themselves"
    return None if code == 0 else "could not send the web port to Genesis (%s)" % out[-200:]


def _portal_off(device, port):
    if os.path.exists(PORTAL_CONF):
        _run(["rm", "-f", PORTAL_CONF], root=True)
    nft, ipt = shutil.which("nft") or "/usr/sbin/nft", shutil.which("iptables") or "/usr/sbin/iptables"
    if os.path.exists(nft):
        _run([nft, "delete", "table", "ip", TABLE], root=True)
    elif os.path.exists(ipt) and device:
        _run([ipt, "-t", "nat", "-D", "PREROUTING", "-i", device, "-p", "tcp", "--dport", "80",
              "-j", "REDIRECT", "--to-ports", str(port)], root=True)


ELSEWHERE = {
    "win32": "Windows makes a hotspot from Settings, Network and internet, Mobile hotspot. On most PCs it shares "
             "a connection, so it needs one to start.",
    "darwin": "A Mac makes a network from System Settings, General, Sharing, Internet Sharing. It shares a "
              "connection, so it needs one to start.",
}


def start(args):
    from .fetch import vault_path
    vault = vault_path(args)
    if not system().startswith("linux"):
        print("genesis hotspot runs on a Raspberry Pi or a Linux PC.\n" + ELSEWHERE.get(system(), "")
              + "\nAny WiFi that the computer and the phones share works as well: a router, or a phone's own hotspot. "
                "Then point the phone's camera at the code on the Genesis home page.")
        return 2
    nmcli = shutil.which("nmcli")
    if not nmcli:
        print("genesis hotspot needs NetworkManager, which Raspberry Pi OS has had since Bookworm (2023).\n"
              "On another Linux, install it, or share any WiFi with the phones and use the code on the home page.")
        return 2
    device = getattr(args, "device", None) or wifi_device(nmcli)
    if not device:
        print("No WiFi adapter found. `nmcli device` lists what this machine has.")
        return 2
    name, password = settings(vault, getattr(args, "name", None), getattr(args, "password", None))
    log("starting the hotspot %s on %s. This machine leaves the WiFi it was on until: genesis hotspot stop" % (name, device))
    _run([nmcli, "connection", "delete", CONNECTION])  # one left from before is made again, never doubled
    code, out = _run([nmcli, "device", "wifi", "hotspot", "ifname", device, "con-name", CONNECTION,
                      "ssid", name, "password", password])
    if code != 0 and "not authorized" in out.lower():
        code, out = _run([nmcli, "device", "wifi", "hotspot", "ifname", device, "con-name", CONNECTION,
                          "ssid", name, "password", password], root=True)
    if code != 0:
        print("NetworkManager could not start the hotspot: %s" % out[-400:])
        return 1
    _run([nmcli, "connection", "modify", CONNECTION, "connection.autoconnect", "no"])  # never on by itself after a restart
    address = _address(nmcli, device) or "10.42.0.1"
    port, portal = dashboard_port(), not getattr(args, "no_portal", False)
    if portal:
        problem = _portal_on(nmcli, device, address, port)
        if problem:
            log("phones are not sent to Genesis by themselves: %s. They open it from the code or the address." % problem)
            portal = False
    write_json(_marker(), {"name": name, "password": password, "address": address, "device": device,
                           "portal": portal, "port": port, "started": time.time()})
    url = "http://%s:%d/" % (address, port)
    print("\nThe hotspot is on.\n  WiFi name:  %s\n  Password:   %s\n  Genesis:    %s\n" % (name, password, url))
    print("On a phone: join %s with that password%s. The Genesis home page shows a code a phone camera "
          "joins the WiFi with." % (name, ", and Genesis opens by itself" if portal else ", then open " + url))
    return 0


def stop(args):
    if not system().startswith("linux"):
        print(ELSEWHERE.get(system(), "genesis hotspot runs on Linux."))
        return 2
    spot = active() or {}
    nmcli = shutil.which("nmcli")
    if nmcli:
        _run([nmcli, "connection", "down", CONNECTION])
        _run([nmcli, "connection", "delete", CONNECTION])
    _portal_off(spot.get("device"), spot.get("port") or dashboard_port())
    if os.path.exists(_marker()):
        os.remove(_marker())
    print("The hotspot is off. The WiFi goes back to the network it knew.")
    return 0


def status(args):
    spot = active()
    if not spot:
        print("No hotspot running. Start one with: genesis hotspot")
        return 1
    print("Hotspot %s on %s, password %s, Genesis at http://%s:%d/%s" % (
        spot["name"], spot["device"], spot["password"], spot["address"], spot["port"],
        ", phones sent there by themselves" if spot.get("portal") else ""))
    return 0


def main(args):
    return {"start": start, "stop": stop, "status": status}[args.action or "start"](args)
