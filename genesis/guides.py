"""Guides Genesis writes itself, kept in the repository as Markdown so they read well on GitHub, and put on the
drive's shelves as pages a browser opens with no internet: guides/<shelf>/<name>.md becomes
vault/documents/<shelf>/<name>.html.

The Markdown is the plain kind these guides use: headings, paragraphs, lists, bold and links. Nothing else.
"""

import html
import os
import re

from .util import ROOT

GUIDES = os.path.join(ROOT, "guides")

STYLE = """
body { margin: 0; background: #fbfaf7; color: #1d1d1b; font: 17px/1.55 Georgia, "Times New Roman", serif; }
main { max-width: 720px; margin: 0 auto; padding: 32px 22px 64px; }
h1 { font-size: 30px; line-height: 1.2; margin: 0 0 14px; }
h2 { font-size: 22px; margin: 38px 0 8px; padding-top: 10px; border-top: 2px solid #1d1d1b; }
h3 { font-size: 18px; margin: 24px 0 6px; }
p, li { margin: 8px 0; }
ul, ol { padding-left: 26px; }
strong { color: #000; }
a { color: #8a3b12; }
@media (prefers-color-scheme: dark) { body { background: #111; color: #e8e6e1; } strong { color: #fff; } h2 { border-color: #e8e6e1; } a { color: #f0a46e; } }
@media print { body { background: #fff; color: #000; font-size: 12pt; } main { max-width: none; padding: 0; } h2 { break-after: avoid; } li { break-inside: avoid; } }
"""


def _inline(text):
    out = html.escape(text, quote=False)
    out = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", out)
    out = re.sub(r"\[([^\]]+)\]\(([^)\s]+)\)", lambda m: '<a href="%s">%s</a>' % (html.escape(m.group(2)), m.group(1)), out)
    return out


def render(markdown):
    """The page for a guide: (its title, the whole HTML)."""
    body, title, para, items, kind = [], "", [], [], None

    def flush_para():
        if para:
            body.append("<p>%s</p>" % _inline(" ".join(para)))
            para.clear()

    def flush_list():
        nonlocal kind
        if items:
            body.append("<%s>%s</%s>" % (kind, "".join("<li>%s</li>" % _inline(i) for i in items), kind))
            items.clear()
        kind = None

    for line in markdown.splitlines():
        stripped = line.strip()
        heading = re.match(r"^(#{1,3})\s+(.*)$", stripped)
        bullet = re.match(r"^[*-]\s+(.*)$", stripped)
        number = re.match(r"^\d+\.\s+(.*)$", stripped)
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
    flush_para()
    flush_list()
    first = next((b for b in body if b.startswith("<p>")), "")
    about = ""
    for sentence in re.split(r"(?<=[.!?])\s+", html.unescape(re.sub(r"<[^>]+>", "", first))):
        if about and len(about) + len(sentence) > 160:
            break
        about = (about + " " + sentence).strip()  # whole sentences only, so the shelf never cuts one in half
    page = ('<!DOCTYPE html>\n<html lang="en"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width, initial-scale=1">'
            '<title>%s</title><meta name="description" content="%s"><style>%s</style></head>\n'
            '<body><main>\n%s\n</main></body></html>\n') % (html.escape(title), html.escape(about), STYLE, "\n".join(body))
    return title, page


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
    """Put every guide on the drive's shelves as a page. Returns how many pages were written or brought up to date."""
    written = 0
    for shelf, stem, path in found(root):
        with open(path, encoding="utf-8") as f:
            _, page = render(f.read())
        target = os.path.join(vault, "documents", shelf, stem + ".html")
        try:
            with open(target, encoding="utf-8") as f:
                if f.read() == page:
                    continue
        except OSError:
            pass
        os.makedirs(os.path.dirname(target), exist_ok=True)
        with open(target, "w", encoding="utf-8", newline="\n") as f:
            f.write(page)
        written += 1
    return written


def heading(path):
    """The title and the first line of a page on a shelf, read from its head: (\"\", \"\") when it has none."""
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            head = f.read(4096)
    except OSError:
        return "", ""
    title = re.search(r"<title>(.*?)</title>", head, re.S | re.I)
    about = re.search(r'<meta name="description" content="([^"]*)"', head, re.I)
    return (html.unescape(title.group(1).strip()) if title else "", html.unescape(about.group(1)) if about else "")
