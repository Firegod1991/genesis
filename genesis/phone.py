"""Phones on the same network: the addresses this box answers on, and a QR code a camera opens them with.

A phone joins the box's network (its hotspot, or the same WiFi), points its camera at the code on the
front page and lands on Genesis, with every page, the maps, the manuals and the assistant. Nothing is
installed on the phone, and nothing here needs the internet.
"""

import html
import ipaddress
import socket

from .qrcodegen import QrCode


def _usable(ip):
    try:
        a = ipaddress.ip_address(ip)
    except ValueError:
        return False
    return a.version == 4 and not (a.is_loopback or a.is_link_local or a.is_unspecified or a.is_multicast)


def addresses():
    """This machine's IPv4 addresses that other machines on its networks can reach, the likeliest first."""
    found = []
    # The address the machine would leave by: no packet is sent, and it works on a hotspot with no internet.
    for probe in ("10.255.255.255", "192.168.255.255", "172.31.255.255"):
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
                s.connect((probe, 9))
                found.append(s.getsockname()[0])
        except OSError:
            pass
    for name in (socket.gethostname(), socket.gethostname() + ".local"):
        try:
            found += [info[4][0] for info in socket.getaddrinfo(name, None, socket.AF_INET)]
        except OSError:
            pass
    seen, out = set(), []
    for ip in found:
        if _usable(ip) and ip not in seen:
            seen.add(ip)
            out.append(ip)
    private = [ip for ip in out if ipaddress.ip_address(ip).is_private]
    return private + [ip for ip in out if ip not in private]


def links(port, first=None):
    """The addresses as links. `first`, the box's own hotspot address when it runs one, leads."""
    found = addresses()
    if first:
        found = [first] + [ip for ip in found if ip != first]
    return ["http://%s%s/" % (ip, "" if port == 80 else ":%d" % port) for ip in found]


def qr_svg(text, border=3, label=None):
    """A QR code as an SVG picture, black on white so every phone camera reads it, in night vision too."""
    qr = QrCode.encode_text(text, QrCode.Ecc.MEDIUM)
    size = qr.get_size()
    parts = ["M%d,%dh1v1h-1z" % (x + border, y + border) for y in range(size) for x in range(size) if qr.get_module(x, y)]
    full = size + border * 2
    return ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 %d %d" shape-rendering="crispEdges" role="img" '
            'aria-label="%s"><rect width="100%%" height="100%%" fill="#fff"/><path d="%s" fill="#000"/></svg>'
            % (full, full, html.escape(label or "QR code for " + text, quote=True), " ".join(parts)))
