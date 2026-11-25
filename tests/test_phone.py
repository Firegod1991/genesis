"""Phones: the addresses a phone can reach the box on, and a QR code that says exactly that."""

import os
import re
import sys
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from genesis import phone  # noqa: E402
from genesis.qrcodegen import QrCode  # noqa: E402


class PhoneTests(unittest.TestCase):
    def test_only_addresses_another_machine_can_reach(self):
        with mock.patch.object(phone.socket, "getaddrinfo", return_value=[
                (2, 1, 6, "", ("127.0.1.1", 0)), (2, 1, 6, "", ("169.254.3.3", 0)), (2, 1, 6, "", ("192.168.1.20", 0)),
                (2, 1, 6, "", ("192.168.1.20", 0)), (2, 1, 6, "", ("8.8.4.4", 0))]), \
                mock.patch.object(phone.socket, "socket", side_effect=OSError("no route")):
            self.assertEqual(phone.addresses(), ["192.168.1.20", "8.8.4.4"], "private first, each once")
        with mock.patch.object(phone, "addresses", return_value=["10.42.0.1"]):
            self.assertEqual(phone.links(8080), ["http://10.42.0.1:8080/"])
            self.assertEqual(phone.links(80), ["http://10.42.0.1/"])

    def test_the_code_holds_the_address_and_nothing_else(self):
        url = "http://192.168.1.20:8080/"
        svg = phone.qr_svg(url, border=4)
        qr = QrCode.encode_text(url, QrCode.Ecc.MEDIUM)
        dark = {(int(x) - 4, int(y) - 4) for x, y in re.findall(r"M(\d+),(\d+)h1v1h-1z", svg)}
        want = {(x, y) for y in range(qr.get_size()) for x in range(qr.get_size()) if qr.get_module(x, y)}
        self.assertEqual(dark, want)
        self.assertIn('viewBox="0 0 %d %d"' % (qr.get_size() + 8, qr.get_size() + 8), svg)


if __name__ == "__main__":
    unittest.main()
