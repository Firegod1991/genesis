"""A port another program holds: the dashboard moves to a spare one and says so."""

import argparse
import http.server
import os
import shutil
import sys
import tempfile
import threading
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from genesis import box  # noqa: E402


class Page(http.server.BaseHTTPRequestHandler):
    body = b""

    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Length", str(len(self.body)))
        self.end_headers()
        self.wfile.write(self.body)

    def log_message(self, *args):
        pass


def serve(body):
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), type("P", (Page,), {"body": body}))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


class DoorTests(unittest.TestCase):
    def setUp(self):
        self.other = serve(b"<html><title>Some other program</title></html>")
        self.genesis = serve(b"<!doctype html>\n<html lang=\"en\">\n<head>\n<title>Genesis</title>")
        for server in (self.other, self.genesis):
            self.addCleanup(server.server_close)
            self.addCleanup(server.shutdown)
        self.taken, self.ours = self.other.server_address[1], self.genesis.server_address[1]

    def test_only_the_genesis_dashboard_counts_as_genesis(self):
        self.assertTrue(box._ours(self.ours))
        self.assertFalse(box._ours(self.taken))
        closed = serve(b"")
        port = closed.server_address[1]
        closed.shutdown()
        closed.server_close()
        self.assertFalse(box._ours(port), "nothing answering is not Genesis either")

    def test_the_dashboard_moves_off_a_port_another_program_holds(self):
        free = serve(b"")
        spare = free.server_address[1]
        free.shutdown()
        free.server_close()
        self.assertEqual(box.door(spare), spare, "a free port is used as asked")
        self.assertEqual(box.door(self.ours), self.ours, "Genesis already answering there is where it is")
        with mock.patch.object(box, "SPARE_PORTS", (self.taken, spare)):
            self.assertEqual(box.door(self.taken), spare, "the first spare port that is free")
        with mock.patch.object(box, "SPARE_PORTS", (spare, self.ours)):
            self.assertEqual(box.door(self.taken), spare)
        with mock.patch.object(box, "SPARE_PORTS", (self.ours, spare)):
            self.assertEqual(box.door(self.taken), self.ours, "or the one where Genesis is already up")
        with mock.patch.object(box, "SPARE_PORTS", ()):
            self.assertEqual(box.door(self.taken), self.taken, "with nowhere to go it stays, and the start says what failed")

    def test_a_start_says_which_port_it_took(self):
        top = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, top, ignore_errors=True)
        vault, run = os.path.join(top, "vault"), os.path.join(top, "run")
        os.makedirs(os.path.join(vault, "documents"))
        os.makedirs(run)
        said, spawned = [], []
        env = {k: v for k, v in os.environ.items() if k not in ("DISPLAY", "WAYLAND_DISPLAY", "GENESIS_PORT")}

        def spawn(cmd, env, logname):
            spawned.append(int(env["PORT"]))
            return 4001

        with mock.patch.object(box, "SPARE_PORTS", (self.ours,)), mock.patch.object(box, "DASHBOARD_PORT", self.taken), \
                mock.patch.object(box, "_spawn", side_effect=spawn), mock.patch.object(box, "_run_dir", return_value=run), \
                mock.patch.object(box, "log", side_effect=said.append), mock.patch.object(box.shutil, "which", return_value=None), \
                mock.patch.dict(os.environ, env, clear=True):
            self.assertEqual(box.start(argparse.Namespace(vault=vault, no_browser=True)), 0)
        self.assertIn("another program on this machine is using port %d, so Genesis answers on port %d" % (self.taken, self.ours), said)
        self.assertIn("dashboard is already running on port %d" % self.ours, said)
        self.assertIn("Genesis is up: http://localhost:%d/" % self.ours, said)
        self.assertEqual(spawned, [], "nothing is started twice")


if __name__ == "__main__":
    unittest.main()
