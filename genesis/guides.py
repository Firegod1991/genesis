"""Guides Genesis writes itself, kept in the repository as Markdown so they read well on GitHub, and put on the
drive's shelves as pages a browser opens with no internet: guides/<shelf>/<name>.md becomes
vault/documents/<shelf>/<name>.html.

The Markdown is the plain kind these guides use: headings, paragraphs, lists, tables, bold and links. Nothing else.
All of them together also become one booklet to print and keep in a go bag: vault/documents/pocket/pocket_guides.html.
"""

import html
import os
import re

from .util import ROOT

GUIDES = os.path.join(ROOT, "guides")
# The booklet runs in the order a person in trouble needs them. Guides not named here follow, by name.
ORDER = ("first_aid", "water", "fire_and_shelter", "lost", "weather", "knots", "radio_card", "family_plan")
BOOKLET = ("pocket", "pocket_guides")

STYLE = """
body { margin: 0; background: #fbfaf7; color: #1d1d1b; font: 17px/1.55 Georgia, "Times New Roman", serif; }
main { max-width: 720px; margin: 0 auto; padding: 32px 22px 64px; }
h1 { font-size: 30px; line-height: 1.2; margin: 0 0 14px; }
h2 { font-size: 22px; margin: 38px 0 8px; padding-top: 10px; border-top: 2px solid #1d1d1b; }
h3 { font-size: 18px; margin: 24px 0 6px; }
p, li { margin: 8px 0; }
ul, ol { padding-left: 26px; }
table { border-collapse: collapse; margin: 12px 0; width: 100%; font-size: 15px; }
th, td { border: 1px solid #9a968c; padding: 5px 8px; text-align: left; vertical-align: top; }
th { background: #ece9e1; }
.guide + .guide { break-before: page; margin-top: 64px; }
.contents li { margin: 4px 0; }
.blank { display: block; border-bottom: 1px solid #9a968c; height: 1.7em; }
.blank + .blank { margin-top: 2px; }
strong { color: #000; }
a { color: #8a3b12; }
@media (prefers-color-scheme: dark) { body { background: #111; color: #e8e6e1; } strong { color: #fff; } h2 { border-color: #e8e6e1; } a { color: #f0a46e; } th { background: #222; } }
@media print { body { background: #fff; color: #000; font-size: 12pt; } main { max-width: none; padding: 0; } h2 { break-after: avoid; } li { break-inside: avoid; } }
"""


def _inline(text):
    out = html.escape(text, quote=False)
    out = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", out)
    out = re.sub(r"\[([^\]]+)\]\(([^)\s]+)\)", lambda m: '<a href="%s">%s</a>' % (html.escape(m.group(2)), m.group(1)), out)
    return out


def _item(text):
    """A list item. One that ends with a colon is a line to fill in, so it gets room to write on paper."""
    return _inline(text) + ('<span class="blank"></span><span class="blank"></span>' if text.endswith(":") else "")


def _row(line):
    return [c.strip() for c in line.strip().strip("|").split("|")]


def parts(markdown):
    """A guide read into (its title, its first sentences, the HTML of its body)."""
    body, title, para, items, kind, table = [], "", [], [], None, []

    def flush_table():
        rows = [r for r in table if not all(set(c) <= set("-: ") for c in r)]  # drop the |---| line
        if rows:
            head, rest = rows[0], rows[1:]
            body.append("<table><thead><tr>%s</tr></thead><tbody>%s</tbody></table>" % (
                "".join("<th>%s</th>" % _inline(c) for c in head),
                "".join("<tr>%s</tr>" % "".join("<td>%s</td>" % _inline(c) for c in r) for r in rest)))
        table.clear()

    def flush_para():
        if para:
            body.append("<p>%s</p>" % _inline(" ".join(para)))
            para.clear()

    def flush_list():
        nonlocal kind
        if items:
            body.append("<%s>%s</%s>" % (kind, "".join("<li>%s</li>" % _item(i) for i in items), kind))
            items.clear()
        kind = None

    for line in markdown.splitlines():
        stripped = line.strip()
        heading = re.match(r"^(#{1,3})\s+(.*)$", stripped)
        bullet = re.match(r"^[*-]\s+(.*)$", stripped)
        number = re.match(r"^\d+\.\s+(.*)$", stripped)
        if stripped.startswith("|"):
            flush_para()
            flush_list()
            table.append(_row(stripped))
            continue
        flush_table()
        if not stripped:
            flush_para()
            flush_list()
        elif heading:
            flush_para()
            flush_list()
            level, text = len(heading.group(1)), heading.group(2)
            title = title or text
            body.append("<h%d>%s</h%d>" % (level, _inline(text), level))
        elif bullet or number:
            flush_para()
            want = "ul" if bullet else "ol"
            if kind and kind != want:
                flush_list()
            kind = want
            items.append((bullet or number).group(1))
        elif items and line.startswith((" ", "\t")):
            items[-1] += " " + stripped  # a list item that runs onto the next line
        else:
            flush_list()
            para.append(stripped)
    flush_table()
    flush_para()
    flush_list()
    first = next((b for b in body if b.startswith("<p>")), "")
    about = ""
    for sentence in re.split(r"(?<=[.!?])\s+", html.unescape(re.sub(r"<[^>]+>", "", first))):
        if about and len(about) + len(sentence) > 160:
            break
        about = (about + " " + sentence).strip()  # whole sentences only, so the shelf never cuts one in half
    return title, about, "\n".join(body)


def page(title, about, body):
    return ('<!DOCTYPE html>\n<html lang="en"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width, initial-scale=1">'
            '<title>%s</title><meta name="description" content="%s"><style>%s</style></head>\n'
            '<body><main>\n%s\n</main></body></html>\n') % (html.escape(title), html.escape(about), STYLE, body)


def render(markdown):
    """The page for a guide: (its title, the whole HTML)."""
    title, about, body = parts(markdown)
    return title, page(title, about, body)


def booklet(root=GUIDES):
    """Every guide in one page, a contents list first and each guide on new paper when printed."""
    found_ = found(root)
    rank = {stem: i for i, stem in enumerate(ORDER)}
    found_.sort(key=lambda g: (rank.get(g[1], len(ORDER)), g[1]))
    contents, sections = [], []
    for n, (_, stem, path) in enumerate(found_, 1):
        with open(path, encoding="utf-8") as f:
            title, _, body = parts(f.read())
        anchor = "g%d" % n
        contents.append('<li><a href="#%s">%s</a></li>' % (anchor, _inline(title)))
        sections.append('<section class="guide" id="%s">\n%s\n</section>' % (anchor, body))
    title = "Genesis pocket guides"
    about = "Every Genesis pocket guide in one booklet, to print and keep in a go bag."
    front = ('<h1>%s</h1><p>%s Print it now, while the power is on, and keep a copy with the first aid kit. '
             'Practice what is in it before you need it.</p><ol class="contents">%s</ol>') % (title, about, "".join(contents))
    return title, page(title, about, front + "\n" + "\n".join(sections))


def found(root=GUIDES):
    """Every guide in the repository: (its shelf, its file name without .md, its path)."""
    out = []
    for shelf in sorted(os.listdir(root)) if os.path.isdir(root) else []:
        folder = os.path.join(root, shelf)
        for name in sorted(os.listdir(folder)) if os.path.isdir(folder) else []:
            if name.endswith(".md") and not name.startswith("."):
                out.append((shelf, name[:-3], os.path.join(folder, name)))
    return out


def place(vault, root=GUIDES):
    """Put every guide on the drive's shelves as a page, and the booklet of all of them on the pocket shelf.
    Returns how many pages were written or brought up to date."""
    pages = []
    for shelf, stem, path in found(root):
        with open(path, encoding="utf-8") as f:
            pages.append((shelf, stem, render(f.read())[1]))
    if pages:
        pages.append(BOOKLET + (booklet(root)[1],))
    written = 0
    for shelf, stem, text in pages:
        target = os.path.join(vault, "documents", shelf, stem + ".html")
        try:
            with open(target, encoding="utf-8") as f:
                if f.read() == text:
                    continue
        except OSError:
            pass
        os.makedirs(os.path.dirname(target), exist_ok=True)
        with open(target, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
        written += 1
    return written


def heading(path):
    """The title and the first line of a page on a shelf, read from its head: ("", "") when it has none."""
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            head = f.read(4096)
    except OSError:
        return "", ""
    title = re.search(r"<title>(.*?)</title>", head, re.S | re.I)
    about = re.search(r'<meta name="description" content="([^"]*)"', head, re.I)
    return (html.unescape(title.group(1).strip()) if title else "", html.unescape(about.group(1)) if about else "")
