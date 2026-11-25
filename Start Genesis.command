#!/bin/sh
# Start Genesis on this Mac: double click this file. Genesis opens in your browser.
# The first time, it offers to bring down a starter library and the assistant.
# The first double click may be stopped by macOS: right click this file, choose Open, then Open again.
cd "$(dirname "$0")" || exit 1
if ! python3 -c "import sys; sys.exit(sys.version_info < (3, 9))" >/dev/null 2>&1; then
  echo "Genesis needs Python 3.9 or newer. If your Mac just offered to install the command line developer tools,"
  echo "say Install: they bring Python. When that finishes, double click Start Genesis again."
  printf "Press Enter to close this window. "; read -r _ || true
  exit 1
fi
sh bin/genesis start "$@"
rc=$?
echo
if [ "$rc" = 0 ]; then
  echo "Genesis keeps running after this window closes. To stop it, double click Stop Genesis."
else
  echo "Genesis did not start. The lines above say why."
fi
printf "Press Enter to close this window. "; read -r _ || true
exit "$rc"
