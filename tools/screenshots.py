#!/usr/bin/env python3
"""Take the README screenshots from a running dashboard.

    python3 tools/screenshots.py http://127.0.0.1:8765 docs/img

Needs: pip install playwright && python3 -m playwright install chromium
Exits non zero if a page logs a script error, the front door has no tiles or
the map does not draw, so it doubles as a browser test of the dashboard.
"""

import os
import sys

from playwright.sync_api import sync_playwright

url = sys.argv[1].rstrip("/") + "/"
out = sys.argv[2]
position = sys.argv[3] if len(sys.argv) > 3 else "43.7384, 7.4246"
os.makedirs(out, exist_ok=True)
errors = []

with sync_playwright() as p:
    browser = p.chromium.launch(args=["--no-sandbox", "--use-gl=swiftshader", "--enable-unsafe-swiftshader",
                                      "--ignore-gpu-blocklist"])
    page = browser.new_page(viewport={"width": 1320, "height": 900})
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.on("console", lambda m: errors.append(m.text) if m.type == "error" and "WebGL" not in m.text else None)
    page.goto(url)  # the front door: one question box and a tile for every section
    page.wait_for_selector("#tiles a", timeout=30000)
    page.wait_for_timeout(1500)
    tiles = page.locator("#tiles a").count()
    page.screenshot(path=os.path.join(out, "home.png"))
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
for e in errors:
    print("PAGE ERROR " + e[:300])
if errors or tiles < 8 or rows < 3 or drawn < 3:
    sys.exit(1)
