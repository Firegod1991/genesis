"""What the dashboard tells a person: manuals with covers, books, search, notes and the assistant."""

import json
import os
import shutil
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "dashboard"))

WATER = "emergency_disinfection_of_drinking_water_sept2017"
BOOK = "zimgit-water_en_2024-08"
CATALOG = """<feed xmlns="http://www.w3.org/2005/Atom"><entry><title>Water Treatment Library</title>
<summary>Everything about clean water</summary><tags>preppers;_ftindex:yes;_pictures:yes</tags><link type="text/html" href="/content/%s"/>
<link rel="http://opds-spec.org/image/thumbnail" href="/catalog/v2/illustration/abc/?size=48"/></entry></feed>""" % BOOK
# A catalog of its own, so a book or manual that gets a new name upstream never breaks these tests.
LOCKED = [("survival", "zimgit-water_en", {"kind": "zim", "title": "Water Treatment Library", "tags": ["water"],
                                           "files": [{"filename": BOOK + ".zim"}]}),
          ("survival", "epa_water", {"kind": "file", "dest": "documents/water", "title": "EPA: Emergency Disinfection of Drinking Water",
                                     "files": [{"filename": WATER + ".pdf"}]}),
          ("apps", "maps", {"kind": "file", "dest": "apps", "title": "Maps", "files": [{"filename": "maps.apk", "platform": "android"}]})]
FOUND = """<rss><channel><item><title>Boiling</title><link>/content/%s/A/Boiling</link>
<description>...Bring water to a <b>rolling</b> <b>boil</b> for one minute....</description></item></channel></rss>""" % BOOK


class Quiet(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass

    def answer(self, body, kind="application/xml"):
        self.send_response(200)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class Reader(Quiet):
    """Stands in for the Kiwix server."""

    def do_GET(self):
        self.answer((CATALOG if self.path.startswith("/catalog") else FOUND).encode())


class Models(Quiet):
    """Stands in for Ollama."""
    asked = []

    def do_GET(self):
        self.answer(json.dumps({"models": [{"name": n, "size": s} for n, s in (
            ("qwen2.5-coder:7b", 4), ("gemma4:e4b", 9), ("embeddinggemma:latest", 1), ("glm-ocr:latest", 2), ("gemma4:e2b", 7))]}).encode())

    def do_POST(self):
        Models.asked.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
        self.answer(b'{"message": {"content": "Boil it "}}\n{"message": {"content": "for one minute."}}\n{"done": true}\n', "application/x-ndjson")


def serve(handler):
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd


class ShelfTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import server
        cls.server = server
        cls.vault = tempfile.mkdtemp()
        for sub in ("documents/water", "zim", "apps", "scripture/engwebp"):
            os.makedirs(os.path.join(cls.vault, *sub.split("/")))
        for rel in ("documents/water/%s.pdf" % WATER, "zim/%s.zim" % BOOK, "zim/half.zim.part", "apps/maps.apk", "scripture/engwebp/index.htm",
                    "documents/water/.directory", "apps/.directory", "scripture/.directory", "zim/.directory"):
            with open(os.path.join(cls.vault, *rel.split("/")), "w") as f:
                f.write("x")
        cls.reader, cls.models, cls.httpd = serve(Reader), serve(Models), serve(server.Handler)
        cls.base = "http://127.0.0.1:%d" % cls.httpd.server_address[1]
        cls.patches = [mock.patch.object(server, "VAULT", cls.vault), mock.patch.object(server, "NOTES", os.path.join(cls.vault, "files", "notes")),
                       mock.patch.object(server, "KIWIX", [cls.reader.server_address]), mock.patch.object(server, "OLLAMA", [cls.models.server_address]),
                       mock.patch.object(server.catalog, "all_locked", side_effect=lambda packs=None: [e for e in LOCKED if not packs or e[0] in packs])]
        for p in cls.patches:
            p.start()

    @classmethod
    def tearDownClass(cls):
        for p in cls.patches:
            p.stop()
        for httpd in (cls.reader, cls.models, cls.httpd):
            httpd.shutdown()
            httpd.server_close()
        cls.server._cache.clear()
        shutil.rmtree(cls.vault, ignore_errors=True)

    def setUp(self):
        self.server._cache.clear()

    def call(self, path, data=None, kind="application/json"):
        req = urllib.request.Request(self.base + path, data=None if data is None else json.dumps(data).encode(),
                                     headers={"Content-Type": kind} if data is not None else {})
        try:
            with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(req, timeout=10) as r:
                return r.status, r.read()
        except urllib.error.HTTPError as e:
            return e.code, e.read()

    def get(self, path):
        return json.loads(self.call(path)[1])

    def test_a_manual_has_its_title_and_a_cover_once_one_is_drawn(self):
        manual = self.get("/api/shelves")["water"]
        self.assertEqual([m["title"] for m in manual], ["EPA: Emergency Disinfection of Drinking Water"], "no folder pictures on the shelf")
        self.assertIsNone(manual[0]["cover"])
        cover = os.path.join(self.vault, ".genesis", "covers", "documents", "water")
        os.makedirs(cover)
        with open(os.path.join(cover, WATER + ".jpg"), "wb") as f:
            f.write(b"jpeg")
        self.server._cache.clear()
        manual = self.get("/api/shelves")["water"][0]
        self.assertEqual(manual["cover"], "/covers/documents/water/%s.jpg" % WATER)
        self.assertEqual(self.call(manual["cover"]), (200, b"jpeg"))
        self.assertEqual(self.call(manual["href"])[0], 200)

    def test_a_book_opens_in_the_reader_with_its_picture(self):
        shelf = self.get("/api/books")
        self.assertTrue(shelf["reader"])
        self.assertEqual([(b["title"], b["about"], b["path"], b["icon"], b["pack"], b["search"]) for b in shelf["books"]],
                         [("Water Treatment Library", "Everything about clean water", "/content/" + BOOK,
                           "/catalog/v2/illustration/abc/?size=48", "survival", True)], "whole books only")

    def test_without_the_reader_the_books_are_still_listed(self):
        with mock.patch.object(self.server, "KIWIX", [("127.0.0.1", 1)]):
            shelf = self.get("/api/books")
        self.assertFalse(shelf["reader"])
        self.assertEqual([(b["title"], b["path"]) for b in shelf["books"]], [("Water Treatment Library", None)])

    def test_one_question_searches_the_manuals_the_books_and_the_pages_inside_them(self):
        hits = self.get("/api/find?q=boil%20water")["hits"]
        self.assertEqual([h["kind"] for h in hits], ["manual", "book", "page"])
        self.assertEqual(hits[2], {"kind": "page", "title": "Boiling", "path": "/content/%s/A/Boiling" % BOOK,
                                   "text": "Bring water to a rolling boil for one minute", "book": "Water Treatment Library"})
        self.assertEqual(self.get("/api/find?q=a")["hits"], [])

    def test_notes_are_kept_on_the_drive_and_nowhere_else(self):
        self.assertEqual(self.call("/api/notes/Well%20log.md", {"text": "40 feet\n"})[0], 200)
        self.assertEqual([n["name"] for n in self.get("/api/notes")], ["Well log.md"])
        self.assertEqual(self.get("/api/notes/Well%20log.md")["text"], "40 feet\n")
        for bad in ("..%2F..%2Fevil.md", "evil.sh", ".hidden.md"):
            self.assertEqual(self.call("/api/notes/" + bad, {"text": "x"})[0], 400, bad)
        self.assertEqual(self.call("/api/notes/form.md", {"text": "x"}, kind="text/plain")[0], 400, "only JSON, so no other site can post")
        self.assertEqual(os.listdir(os.path.join(self.vault, "files", "notes")), ["Well log.md"])

    def test_the_assistant_answers_from_the_local_model_with_the_excerpts_it_was_given(self):
        self.assertEqual(self.get("/api/ai/models"), {"running": True, "models": [
            {"name": "gemma4:e2b", "bytes": 7, "use": "everyday questions"}, {"name": "gemma4:e4b", "bytes": 9, "use": "everyday questions"},
            {"name": "qwen2.5-coder:7b", "bytes": 4, "use": "writing code"}]}, "the smallest everyday model first, and only ones that can talk")
        status, body = self.call("/api/ai/chat", {"model": "gemma4:e2b", "messages": [{"role": "user", "content": "water?"}],
                                                  "excerpts": [{"book": "EPA", "text": "boil one minute"}]})
        self.assertEqual(status, 200)
        said = "".join(json.loads(line).get("message", {}).get("content", "") for line in body.decode().splitlines() if line)
        self.assertEqual(said, "Boil it for one minute.")
        asked = Models.asked[-1]
        self.assertEqual((asked["model"], asked["stream"], asked["messages"][-1]), ("gemma4:e2b", True, {"role": "user", "content": "water?"}))
        self.assertIn("[EPA] boil one minute", asked["messages"][0]["content"])
        self.assertIs(asked["think"], False, "no reasoning out loud first: on a small computer that costs minutes")
        self.call("/api/ai/chat", {"model": "deepseek-r1:8b", "messages": [{"role": "user", "content": "why?"}]})
        self.assertNotIn("think", Models.asked[-1], "the model kept for hard problems reasons as it was built to")
        with mock.patch.object(self.server, "OLLAMA", [("127.0.0.1", 1)]):
            self.assertEqual(self.call("/api/ai/chat", {"model": "x", "messages": []})[0], 503)
            self.assertEqual(self.get("/api/ai/models"), {"running": False, "models": []})

    def test_started_from_the_drive_no_name_is_ever_looked_up(self):
        asked = []
        with mock.patch.object(self.server, "BARE", True), \
                mock.patch.object(self.server, "_port_open", side_effect=lambda host, port: asked.append(host)):
            self.server.services()
        self.assertEqual(set(asked), {"127.0.0.1"}, "a lookup with no network behind it can hang for seconds")

    def test_programs_for_other_machines_are_listed(self):
        self.assertEqual([a["href"] for a in self.get("/api/software")["apps"]], ["/apps/maps.apk"])

    def test_the_folder_pictures_genesis_ready_writes_are_never_taken_for_content(self):
        with open(os.path.join(self.vault, "scripture", "engwebp", "genesis.json"), "w") as f:
            json.dump({"title": "World English Bible", "language": "English", "lang": "eng"}, f)
        self.assertEqual([t["id"] for t in self.get("/api/scripture")], ["engwebp"])
        drive = self.get("/api/vault")
        self.assertEqual((drive["apps"], drive["documents"]), (["maps.apk"], ["documents/water/%s.pdf" % WATER]))

    def test_a_phone_gets_an_address_and_a_code_its_camera_reads(self):
        with mock.patch.object(self.server, "BARE", True), mock.patch.object(self.server, "PORT", 8765), \
                mock.patch.object(self.server.phone, "addresses", return_value=["192.168.4.1", "10.0.0.7"]):
            got = self.get("/api/phone")
        self.assertEqual(got["links"], ["http://192.168.4.1:8765/", "http://10.0.0.7:8765/"])
        self.assertTrue(got["qr"].startswith("<svg") and "QR code for http://192.168.4.1:8765/" in got["qr"])
        with mock.patch.object(self.server, "BARE", False):
            self.assertEqual(self.get("/api/phone")["links"], [], "inside a container its own addresses reach no phone")
        status, body = self.call("/manifest.webmanifest")
        self.assertEqual((status, json.loads(body)["display"]), (200, "standalone"), "a phone keeps it as an app")
        for name, size in (("app-192", 192), ("app-512", 512), ("apple-touch-icon", 180)):
            status, png = self.call("/icons/%s.png" % name)
            self.assertEqual((status, png[:8], int.from_bytes(png[16:20], "big")), (200, b"\x89PNG\r\n\x1a\n", size), name)


if __name__ == "__main__":
    unittest.main()
