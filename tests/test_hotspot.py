"""The box's own WiFi: the commands NetworkManager is given, the password kept on the drive, phones sent to
Genesis when they join, the code a phone camera joins with, and what other systems are told instead."""

import json
import os
import shutil
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "dashboard"))

from genesis import hotspot  # noqa: E402
from genesis.__main__ import build_parser  # noqa: E402


class Fake:
    """Stands in for nmcli, sudo, nft and tee, and writes down what it was asked."""

    def __init__(self, refuse=(), say=None):
        self.calls, self.refuse, self.say = [], refuse, say or {}

    def __call__(self, cmd, root=False, data=None):
        self.calls.append((cmd, root, data))
        words = " ".join(cmd)
        if any(r in words for r in self.refuse):
            return 1, "Error: Not authorized to control networking." if "hotspot" in words and not root else "denied"
        for key, out in self.say.items():
            if key in words:
                return 0, out
        return 0, ""


class HotspotTests(unittest.TestCase):
    def setUp(self):
        self.vault = tempfile.mkdtemp()
        self.run_dir = tempfile.mkdtemp()
        for d in (self.vault, self.run_dir):
            self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        os.makedirs(os.path.join(self.vault, ".genesis"))
        for p in (mock.patch("genesis.box._run_dir", return_value=self.run_dir), mock.patch.object(hotspot, "system", return_value="linux"),
                  mock.patch.object(hotspot.shutil, "which", side_effect=lambda n: "/usr/bin/" + n),
                  mock.patch.object(hotspot.os.path, "exists", side_effect=lambda p: p == "/usr/bin/nft" or os.path.isfile(p)),
                  mock.patch.object(hotspot, "dashboard_port", return_value=8080), mock.patch.object(hotspot.time, "sleep")):
            p.start()
            self.addCleanup(p.stop)

    def args(self, *more):
        return build_parser().parse_args(["hotspot"] + list(more) + ["--vault", self.vault])

    def test_the_code_a_phone_camera_joins_with(self):
        self.assertEqual(hotspot.join_code("Genesis", "river-pine-42"), "WIFI:T:WPA;S:Genesis;P:river-pine-42;;")
        self.assertEqual(hotspot.join_code('My;Net', 'a:b,c"d\\e'), 'WIFI:T:WPA;S:My\\;Net;P:a\\:b\\,c\\"d\\\\e;;')

    def test_the_password_is_made_once_and_kept_on_the_drive(self):
        name, password = hotspot.settings(self.vault)
        self.assertEqual(name, "Genesis")
        self.assertTrue(8 <= len(password) <= 63)
        self.assertEqual(hotspot.settings(self.vault), (name, password), "phones that joined once join again")
        self.assertEqual(hotspot.settings(self.vault, name="Shelter", password="longenough"), ("Shelter", "longenough"))
        self.assertEqual(hotspot.settings(self.vault), ("Shelter", "longenough"))
        with self.assertRaises(SystemExit):
            hotspot.settings(self.vault, password="short")

    def test_start_asks_networkmanager_and_sends_phones_to_genesis(self):
        fake = Fake(say={"DEVICE,TYPE": "eth0:ethernet\np2p-dev-wlan0:wifi-p2p\nwlan0:wifi\nlo:loopback",
                         "IP4.ADDRESS": "10.42.0.1/24"})
        with mock.patch.object(hotspot, "_run", side_effect=fake):
            self.assertEqual(hotspot.main(self.args("--password", "river-pine-42")), 0)
        said = [" ".join(c) for c, _, _ in fake.calls]
        self.assertIn("/usr/bin/nmcli connection delete genesis-hotspot", said)
        self.assertIn("/usr/bin/nmcli device wifi hotspot ifname wlan0 con-name genesis-hotspot ssid Genesis password river-pine-42", said)
        self.assertIn("/usr/bin/nmcli connection modify genesis-hotspot connection.autoconnect no", said, "never on by itself")
        tee = next((root, data) for c, root, data in fake.calls if c[0] == "tee")
        self.assertEqual(tee, (True, "# Written by genesis hotspot: every name a phone on the hotspot looks up leads to this box.\n"
                                     "address=/#/10.42.0.1\n"))
        rules = next(data for c, _, data in fake.calls if c[:2] == ["/usr/bin/nft", "-f"])
        self.assertIn('iifname "wlan0" tcp dport 80 redirect to :8080', rules)
        spot = hotspot.active()
        self.assertEqual((spot["name"], spot["password"], spot["address"], spot["device"], spot["portal"]),
                         ("Genesis", "river-pine-42", "10.42.0.1", "wlan0", True))
        fake.calls.clear()
        with mock.patch.object(hotspot, "_run", side_effect=fake), mock.patch.object(hotspot.os.path, "exists", return_value=True), \
                mock.patch.object(hotspot.os, "remove", side_effect=lambda p: os.unlink(p) if p.startswith(self.run_dir) else None):
            self.assertEqual(hotspot.main(self.args("stop")), 0)
        said = [" ".join(c) for c, _, _ in fake.calls]
        self.assertEqual(said[:2], ["/usr/bin/nmcli connection down genesis-hotspot", "/usr/bin/nmcli connection delete genesis-hotspot"])
        self.assertIn("rm -f " + hotspot.PORTAL_CONF, said)
        self.assertIn("/usr/bin/nft delete table ip genesis_portal", said)
        self.assertIsNone(hotspot.active())

    def test_without_the_administrator_the_hotspot_still_runs(self):
        fake = Fake(refuse=("hotspot ifname", "tee"), say={"DEVICE,TYPE": "wlan0:wifi", "IP4.ADDRESS": "10.42.0.1/24"})
        with mock.patch.object(hotspot, "_run", side_effect=fake):
            self.assertEqual(hotspot.main(self.args()), 1, "NetworkManager said no twice")
        tries = [root for c, root, _ in fake.calls if "hotspot" in c]
        self.assertEqual(tries, [False, True], "asked as the administrator after the first no")
        fake = Fake(refuse=("tee",), say={"DEVICE,TYPE": "wlan0:wifi", "IP4.ADDRESS": "10.42.0.1/24"})
        with mock.patch.object(hotspot, "_run", side_effect=fake):
            self.assertEqual(hotspot.main(self.args()), 0)
        self.assertFalse(hotspot.active()["portal"], "phones open it from the code instead")
        fake = Fake(say={"DEVICE,TYPE": "wlan0:wifi", "IP4.ADDRESS": "10.42.0.1/24"})
        with mock.patch.object(hotspot, "_run", side_effect=fake):
            self.assertEqual(hotspot.main(self.args("--no-portal")), 0)
        self.assertFalse([c for c, _, _ in fake.calls if c[0] in ("tee", "/usr/bin/nft")])

    def test_other_systems_are_told_what_to_do_instead(self):
        for system in ("win32", "darwin"):
            with mock.patch.object(hotspot, "system", return_value=system), mock.patch("builtins.print") as said:
                self.assertEqual(hotspot.main(self.args()), 2)
            self.assertIn("hotspot", said.call_args_list[0][0][0].lower())
        with mock.patch.object(hotspot.shutil, "which", return_value=None), mock.patch("builtins.print") as said:
            self.assertEqual(hotspot.main(self.args()), 2)
        self.assertIn("NetworkManager", said.call_args[0][0])
        with mock.patch.object(hotspot, "_run", side_effect=Fake(say={"DEVICE,TYPE": "eth0:ethernet"})), \
                mock.patch("builtins.print") as said:
            self.assertEqual(hotspot.main(self.args()), 2)
        self.assertIn("No WiFi", said.call_args[0][0])


class PortalTests(unittest.TestCase):
    def test_a_phone_checking_for_a_sign_in_page_lands_on_genesis(self):
        import server
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        self.addCleanup(httpd.server_close)
        self.addCleanup(httpd.shutdown)
        port = httpd.server_address[1]
        spot = {"name": "Genesis", "password": "river-pine-42", "address": "10.42.0.1", "device": "wlan0", "portal": True}
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())

        def get(path, host):
            req = urllib.request.Request("http://127.0.0.1:%d%s" % (port, path), headers={"Host": host})
            try:
                with opener.open(req, timeout=10) as r:
                    return r.status, r.headers.get("Location"), r.read()
            except urllib.error.HTTPError as e:
                return e.code, e.headers.get("Location"), e.read()
        with mock.patch.object(server, "BARE", True), mock.patch.object(server, "PORT", 8080), \
                mock.patch.object(server.hotspot, "active", return_value=spot), \
                mock.patch.object(server.phone, "addresses", return_value=["192.168.1.5", "10.42.0.1"]):
            server._cache.clear()
            for probe in ("connectivitycheck.gstatic.com", "captive.apple.com", "www.msftconnecttest.com:80"):
                self.assertEqual(get("/generate_204", probe)[:2], (302, "http://10.42.0.1:8080/"), probe)
            self.assertEqual(get("/", "10.42.0.1:8080")[0], 200, "the box by its address is the box")
            self.assertEqual(get("/", "localhost:8080")[0], 200)
            status, _, body = get("/api/phone", "10.42.0.1:8080")
            got = json.loads(body)
            self.assertEqual(got["links"][0], "http://10.42.0.1:8080/", "the hotspot's own address first")
            self.assertEqual((got["wifi"]["name"], got["wifi"]["password"]), ("Genesis", "river-pine-42"))
            self.assertIn("QR code that joins the Genesis WiFi", got["wifi"]["qr"])
            self.assertNotIn("river-pine-42", got["wifi"]["qr"].split(">")[0], "the password is in the picture, not its label")
        with mock.patch.object(server, "BARE", True), mock.patch.object(server.hotspot, "active", return_value=dict(spot, portal=False)):
            server._cache.clear()
            self.assertEqual(get("/", "connectivitycheck.gstatic.com")[0], 200, "no portal asked for: nothing redirected")
        server._cache.clear()


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kw):
        return None


if __name__ == "__main__":
    unittest.main()
