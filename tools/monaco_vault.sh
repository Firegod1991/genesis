#!/usr/bin/env bash
# A small real vault for the tests: survival layers and a map for Monaco.
#
# Geofabrik turns away some cloud machines, GitHub's among them. That is their call and says
# nothing about Genesis, so each step has a second way to reach the same data.
set -euo pipefail

scratch="${RUNNER_TEMP:-${TMPDIR:-/tmp}}"

if ! python3 -m genesis poi build --region europe/monaco --replace; then
  echo "Geofabrik did not serve the extract. Taking it from the OpenStreetMap France mirror."
  curl -fsSL --retry 3 --retry-delay 5 -A "GenesisCI/1 (+https://github.com/Firegod1991/genesis)" \
    -o "$scratch/monaco.osm.pbf" https://download.openstreetmap.fr/extracts/europe/monaco-latest.osm.pbf
  python3 -m genesis poi build --pbf "$scratch/monaco.osm.pbf" --replace
fi

if ! python3 -m genesis maps region --region europe/monaco; then
  echo "Geofabrik did not serve the boundary. Cutting the same area by its bounding box."
  python3 -m genesis maps region --name europe_monaco --bbox 7.40,43.72,7.45,43.76
fi
