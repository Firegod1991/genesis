"""A stand in for 7-Zip in the tests: the three commands the safe uses, with the passphrase on standard input.

Like the real program it reads the passphrase once, and like the real program a bare -p while opening
an archive means an empty passphrase, not "ask me". What it writes is a zip behind one line that holds
a digest of the passphrase: enough to tell the right one from a wrong one, and nothing more.
"""

import hashlib
import io
import os
import sys
import zipfile


def mark(secret):
    return b"STUB7Z " + hashlib.sha256(secret.encode("utf-8")).hexdigest().encode() + b"\n"


def main(argv):
    command, rest = argv[0], argv[1:]
    target = [a for a in rest if not a.startswith("-")][0]
    secret = sys.stdin.readline().rstrip("\r\n")
    if command == "a":
        packed = io.BytesIO()
        with zipfile.ZipFile(packed, "w") as z:
            for here, _, names in os.walk("."):
                for name in names:
                    z.write(os.path.join(here, name), os.path.relpath(os.path.join(here, name), "."))
        with open(target, "wb") as f:
            f.write(mark(secret if "-p" in rest else "") + packed.getvalue())
        return 0
    with open(target, "rb") as f:
        head, body = f.readline(), f.read()
    if head != mark("" if "-p" in rest else secret):
        sys.stderr.write("ERROR: %s\nCannot open encrypted archive. Wrong password?\n" % target)
        return 2
    if command == "x":
        with zipfile.ZipFile(io.BytesIO(body)) as z:
            z.extractall([a[2:] for a in rest if a.startswith("-o")][0])
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
