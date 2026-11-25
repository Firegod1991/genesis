#!/bin/sh
# Stop Genesis on this Mac: double click this file.
cd "$(dirname "$0")" || exit 1
sh bin/genesis stop
printf "Press Enter to close this window. "; read -r _ || true
