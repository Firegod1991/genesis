#!/usr/bin/env python3
"""Ask a started Genesis real questions, the way a person at its front door would.

    python3 tools/start_check.py [http://127.0.0.1:8080] [--books 2] [--manuals 1] [--bibles 1]

Run it after `genesis start` on a vault that holds books, manuals and scripture. It exits non zero when
the front door, the book reader, a book's own page, the search inside the books, a manual or scripture
does not answer. It needs nothing but Python, so it runs wherever Genesis does.
"""

import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

args = sys.argv[1:]
want = {"books": 1, "manuals": 1, "bibles": 1}
for name in list(want):
    if "--" + name in args:
        at = args.index("--" + name)
        want[name] = int(args[at + 1])
        del args[at:at + 2]
base = (args[0] if args else "http://127.0.0.1:8080").rstrip("/")
reader = "%s:8081" % base.rsplit(":", 1)[0]
problems = []


def get(url, read=None):
    with urllib.request.urlopen(url, timeout=30) as answer:
        return answer.status, answer.read() if read is None else answer.read(read)


def api(name, **query):
    status, body = get("%s/api/%s%s" % (base, name, ("?" + urllib.parse.urlencode(query)) if query else ""))
    return json.loads(body)


def check(what, ok, detail=""):
    print("%s  %s%s" % ("ok  " if ok else "FAIL", what, ("  (%s)" % detail) if detail else ""))
    if not ok:
        problems.append(what)


try:
    status, page = get(base + "/")
    check("the front door opens", status == 200 and b"What do you need?" in page)

    books = {}
    for _ in range(90):  # the reader comes up a little after the front door
        books = api("books")
        if books.get("reader"):
            break
        time.sleep(1)
    shelf = books.get("books") or []
    check("the book reader is running", bool(books.get("reader")))
    check("the library lists its books", len(shelf) >= want["books"], "%d listed, %d expected" % (len(shelf), want["books"]))
    opened = 0
    for book in shelf:
        try:
            opened += get(reader + book["path"], 4096)[0] == 200
        except (urllib.error.URLError, OSError, KeyError):
            pass
    check("every book opens in the reader", shelf and opened == len(shelf), "%d of %d" % (opened, len(shelf)))

    inside = [h for h in api("find", q="water").get("hits", []) if h.get("kind") == "page"]
    check("a question is answered from the pages inside the books", bool(inside),
          inside[0]["title"] if inside else "no page found for: water")
    if inside:
        check("that page opens", get(reader + inside[0]["path"], 4096)[0] == 200)

    manuals = [m for shelf_ in api("shelves").values() if isinstance(shelf_, list) for m in shelf_ if m.get("href")]
    check("the shelves list their manuals", len(manuals) >= want["manuals"], "%d listed, %d expected" % (len(manuals), want["manuals"]))
    def opens(href):  # a manual is a PDF or a page: a PDF has to start the way a PDF starts
        status, head = get(base + urllib.parse.quote(href), 5)
        return status == 200 and (head.startswith(b"%PDF") if href.lower().endswith(".pdf") else bool(head))

    if manuals:
        first = manuals[0]["href"]
        check("a manual opens", opens(first), first)
        pdfs = [m["href"] for m in manuals if m["href"].lower().endswith(".pdf")]
        if pdfs and pdfs[0] != first:
            check("a PDF manual opens", opens(pdfs[0]), pdfs[0])

    bibles = api("scripture")
    check("scripture is listed", len(bibles) >= want["bibles"], "%d listed, %d expected" % (len(bibles), want["bibles"]))
    if bibles:
        check("a translation opens", get(base + urllib.parse.quote(bibles[0]["href"]), 4096)[0] == 200, bibles[0]["label"])
except (urllib.error.URLError, OSError, ValueError) as e:
    check("Genesis answers", False, "%s: %s" % (type(e).__name__, e))

if problems:
    print("\n%d did not pass: %s" % (len(problems), "; ".join(problems)))
    sys.exit(1)
print("\nGenesis is up and answers from every shelf.")
