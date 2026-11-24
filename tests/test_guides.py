"""Guides written in the repository reach the drive's shelves as pages, and the dashboard shows them by title."""

import os
import shutil
import sys
import tempfile
import unittest

from genesis import guides

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class RenderTests(unittest.TestCase):
    def test_a_guide_becomes_a_page(self):
        title, page = guides.render("# Lost\n\nStop and **think**.\n\n## Steps\n\n1. Sit down.\n2. Drink.\n\n* one\n* two\n  more\n")
        self.assertEqual(title, "Lost")
        self.assertIn("<title>Lost</title>", page)
        self.assertIn('<meta name="description" content="Stop and think.">', page)
        self.assertIn("<p>Stop and <strong>think</strong>.</p>", page)
        self.assertIn("<ol><li>Sit down.</li><li>Drink.</li></ol>", page)
        self.assertIn("<ul><li>one</li><li>two more</li></ul>", page)

    def test_text_cannot_become_markup(self):
        _, page = guides.render("# A <b> & C\n\n<script>alert(1)</script>\n")
        self.assertNotIn("<script>", page)
        self.assertIn("&lt;script&gt;", page)

    def test_tables(self):
        _, page = guides.render("# T\n\n| Water | Bleach |\n|---|---|\n| 1 gallon | 8 drops |\n\nAfter.\n")
        self.assertIn("<table><thead><tr><th>Water</th><th>Bleach</th></tr></thead><tbody><tr><td>1 gallon</td><td>8 drops</td></tr></tbody></table>", page)
        self.assertIn("<p>After.</p>", page)

    def test_links_open(self):
        _, page = guides.render("# T\n\nSee [the map](maps/world.html).\n")
        self.assertIn('<a href="maps/world.html">the map</a>', page)


class PlaceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp)
        self.src = os.path.join(self.tmp, "guides", "navigation")
        os.makedirs(self.src)
        with open(os.path.join(self.src, "lost.md"), "w", encoding="utf-8") as f:
            f.write("# Lost: find your way\n\nStop first.\n")
        self.vault = os.path.join(self.tmp, "vault")

    def test_a_guide_lands_on_its_shelf_once(self):
        root = os.path.join(self.tmp, "guides")
        self.assertEqual(guides.place(self.vault, root), 2, "the guide and the booklet")
        page = os.path.join(self.vault, "documents", "navigation", "lost.html")
        self.assertTrue(os.path.isfile(page))
        self.assertEqual(guides.heading(page), ("Lost: find your way", "Stop first."))
        self.assertEqual(guides.place(self.vault, root), 0, "an unchanged guide is not written again, so the seal holds")

    def test_the_shelf_shows_the_title(self):
        guides.place(self.vault, os.path.join(self.tmp, "guides"))
        sys.path.insert(0, os.path.join(ROOT, "dashboard"))
        import server
        old = server.VAULT
        server.VAULT = self.vault
        try:
            shelf = server.shelves()["navigation"]
        finally:
            server.VAULT = old
        self.assertEqual(shelf[0]["title"], "Lost: find your way")
        self.assertEqual(shelf[0]["note"], "Stop first.")
        self.assertEqual(shelf[0]["href"], "/documents/navigation/lost.html")


class BookletTests(PlaceTests):
    def test_every_guide_is_in_the_booklet_in_order(self):
        with open(os.path.join(self.src, "first_aid.md"), "w", encoding="utf-8") as f:
            f.write("# First aid\n\nPress hard.\n")
        guides.place(self.vault, os.path.join(self.tmp, "guides"))
        with open(os.path.join(self.vault, "documents", "pocket", "pocket_guides.html"), encoding="utf-8") as f:
            book = f.read()
        self.assertLess(book.index("Press hard."), book.index("Stop first."), "first aid comes before getting lost")
        self.assertIn('<a href="#g1">First aid</a>', book)
        self.assertEqual(book.count('<section class="guide"'), 2)


class RepositoryTests(unittest.TestCase):
    def test_every_guide_in_the_repository_renders(self):
        found = guides.found()
        self.assertGreaterEqual(len(found), 8)
        for shelf, stem, path in found:
            with open(path, encoding="utf-8") as f:
                text = f.read()
            title, page = guides.render(text)
            self.assertTrue(title, path)
            self.assertNotIn("|", page.split("<main>")[1].replace("</table>", ""), path + ": a table did not render")
            self.assertIn(stem, guides.ORDER, "every guide has its place in the booklet: " + stem)

    def test_the_bleach_doses_are_the_epa_table(self):
        with open(os.path.join(ROOT, "guides", "water", "water.md"), encoding="utf-8") as f:
            text = f.read()
        for row in ("| 1 quart or 1 liter | 2 drops | 2 drops |", "| 1 gallon | 8 drops | 6 drops |",
                    "| 2 gallons | 16 drops (1/4 teaspoon) | 12 drops (1/8 teaspoon) |",
                    "| 4 gallons | 1/3 teaspoon | 1/4 teaspoon |", "| 8 gallons | 2/3 teaspoon | 1/2 teaspoon |"):
            self.assertIn(row, text)

    def test_the_lost_guide_is_in_the_repository_and_renders(self):
        found = {(shelf, stem) for shelf, stem, _ in guides.found()}
        self.assertIn(("navigation", "lost"), found)
        with open(os.path.join(ROOT, "guides", "navigation", "lost.md"), encoding="utf-8") as f:
            title, page = guides.render(f.read())
        self.assertTrue(title.startswith("Lost"))
        for must in ("Polaris", "shadow", "back bearing", "declination", "Southern Cross", "Three of anything"):
            self.assertIn(must.lower(), page.lower(), must)


if __name__ == "__main__":
    unittest.main()
