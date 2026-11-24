#!/usr/bin/env python3
"""Take the README screenshots from a running dashboard.

    python3 tools/screenshots.py http://127.0.0.1:8765 docs/img
    python3 tools/screenshots.py http://127.0.0.1:8765 docs/img --expect search,manuals,library,reader,phone

Needs: pip install playwright && python3 -m playwright install chromium
Exits non zero if a page logs a script error, the front door has no tiles or
the map does not draw, so it doubles as a browser test of the dashboard. The
pages whose content a vault may not hold (manuals, plants, books, scripture)
are pictured when they have something to show and passed over when they do
not. Name the ones a vault must show with --expect, and a missing one fails too.
"""

import os
import sys

from playwright.sync_api import sync_playwright

args = sys.argv[1:]
expect = []
if "--expect" in args:
    at = args.index("--expect")
    expect = [name for name in args[at + 1].split(",") if name]
    del args[at:at + 2]
url = args[0].rstrip("/") + "/"
out = args[1]
position = args[2] if len(args) > 2 else "43.7384, 7.4246"
os.makedirs(out, exist_ok=True)
errors, pictured, heed = [], {}, [True]
# A page, what on it proves there is something to show, and the picture it becomes.
PAGES = (("manuals", "#manual-shelves .card"), ("plants", "#plant-shelves .card"), ("library", "#library-books .bookcard"),
         ("scripture", "#scripture-list .lang"), ("radio", "#radio-tables table"))


def listen(page):
    page.on("pageerror", lambda e: errors.append(str(e)) if heed[0] else None)
    page.on("console", lambda m: errors.append(m.text) if heed[0] and m.type == "error" and "WebGL" not in m.text else None)


def settled(page, selector, wait=15000):
    """How many of `selector` the page shows once its pictures have loaded. None when it never shows one."""
    try:
        page.wait_for_selector(selector, timeout=wait)
        page.wait_for_function("() => [...document.images].every((i) => i.complete)", timeout=20000)
    except Exception:
        return None
    page.wait_for_timeout(700)
    return page.locator(selector).count()


def picture(page, name, count):
    if count:
        pictured[name] = count
        page.screenshot(path=os.path.join(out, name + ".png"))


with sync_playwright() as p:
    browser = p.chromium.launch(args=["--no-sandbox", "--use-gl=swiftshader", "--enable-unsafe-swiftshader",
                                      "--ignore-gpu-blocklist"])
    page = browser.new_page(viewport={"width": 1320, "height": 900})
    listen(page)
    page.goto(url)  # the front door: one question box and a tile for every section
    page.wait_for_selector("#tiles a", timeout=30000)
    page.wait_for_timeout(1500)
    tiles = page.locator("#tiles a").count()
    page.screenshot(path=os.path.join(out, "home.png"))

    page.fill("#need-q", "clean water")  # one question, every shelf
    page.click("#need button")
    picture(page, "search", settled(page, "#found .hit", wait=40000))

    for view, proof in PAGES:
        page.goto(url + "#" + view)
        picture(page, view, settled(page, proof))
        if view != "library":
            continue
        books = page.locator("#library-books a.bookcard")  # a book the reader has open, shown inside the dashboard
        if books.count():
            knots = books.filter(has_text="Knots")
            heed[0] = False  # the book's own pages are the publisher's: their script errors are not the dashboard's
            (knots if knots.count() else books).first.click()
            try:
                page.wait_for_selector("#read-frame[src]", timeout=15000)
                page.frame_locator("#read-frame").locator("body *").first.wait_for(timeout=30000)
                page.wait_for_timeout(2500)
                picture(page, "reader", 1)
            except Exception:
                pass
            page.goto(url + "#library")
            page.wait_for_timeout(300)
            heed[0] = True

    phone = browser.new_context(viewport={"width": 390, "height": 844}, device_scale_factor=2, is_mobile=True, has_touch=True)
    small = phone.new_page()  # the same front door on a phone joined to the box
    listen(small)
    small.goto(url)
    picture(small, "phone", settled(small, "#tiles a", wait=30000))
    phone.close()

    page.goto(url + "#map")
    page.reload()  # the map view from a fresh load, as a person who bookmarks it gets it
    page.wait_for_function("() => window.genesisMap && window.genesisMap.loaded()", timeout=60000)
    page.fill("#pos", position)
    page.click("#where button")
    page.wait_for_selector("#bearings tbody tr[data-lat]", timeout=30000)
    page.wait_for_timeout(6000)
    rows = page.locator("#bearings tbody tr[data-lat]").count()
    drawn = page.evaluate("() => genesisMap.queryRenderedFeatures({layers: ['poi']}).length")
    page.screenshot(path=os.path.join(out, "dashboard.png"))
    page.click("#night")
    page.wait_for_timeout(5000)
    page.screenshot(path=os.path.join(out, "night.png"))
    browser.close()

print("tiles on the front door: %d, bearing rows: %d, places drawn on the map: %d" % (tiles, rows, drawn))
print("also pictured: %s" % (", ".join("%s (%d)" % kv for kv in sorted(pictured.items())) or "nothing more"))
for e in errors:
    print("PAGE ERROR " + e[:300])
absent = [name for name in expect if name not in pictured]
if absent:
    print("EXPECTED AND NOT SHOWN: " + ", ".join(absent))
if errors or absent or tiles < 8 or rows < 3 or drawn < 3:
    sys.exit(1)
