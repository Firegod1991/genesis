#!/usr/bin/env bash
# Genesis installer for Debian, Ubuntu and Raspberry Pi OS.
#
#   git clone https://github.com/Firegod1991/genesis && cd genesis && bash install.sh
#
# What it does, and nothing else:
#   1. installs git, curl, python3 and osmium-tool from your distribution
#   2. installs Docker with the official convenience script, if Docker is missing (asks first)
#   3. creates the vault folder and links `genesis` into /usr/local/bin
# It never downloads content. You choose that with `genesis fetch`.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VAULT="${GENESIS_VAULT:-$ROOT/vault}"
YES="${GENESIS_YES:-}"

say() { printf '\n== %s\n' "$*"; }
ask() {
  [ -n "$YES" ] && return 0
  read -r -p "$1 [y/N] " reply
  [ "$reply" = "y" ] || [ "$reply" = "Y" ]
}
SUDO=""
if [ "$(id -u)" -ne 0 ]; then
  command -v sudo >/dev/null 2>&1 || { echo "Run as root or install sudo."; exit 1; }
  SUDO="sudo"
fi

if ! command -v apt-get >/dev/null 2>&1; then
  echo "This installer supports Debian based systems (Debian, Ubuntu, Raspberry Pi OS)."
  echo "On anything else install python3, git, curl, docker and osmium-tool yourself, then run bin/genesis doctor."
  exit 1
fi

say "Installing base packages"
$SUDO apt-get update -qq
$SUDO apt-get install -y -qq git curl ca-certificates python3 osmium-tool usbutils

if ! command -v docker >/dev/null 2>&1; then
  say "Docker is not installed"
  if ask "Install Docker now using the official script from get.docker.com?"; then
    tmp="$(mktemp)"
    curl -fsSL https://get.docker.com -o "$tmp"
    $SUDO sh "$tmp"
    rm -f "$tmp"
    if [ -n "$SUDO" ]; then
      $SUDO usermod -aG docker "$USER" || true
      echo "Added $USER to the docker group. Log out and back in for it to take effect."
    fi
  else
    echo "Skipped. The catalog, maps and survival layers work without Docker; the services do not."
  fi
fi

say "Creating the vault at $VAULT"
mkdir -p "$VAULT"

say "Linking the genesis command"
chmod +x "$ROOT/bin/genesis"
$SUDO ln -sf "$ROOT/bin/genesis" /usr/local/bin/genesis

if [ "$VAULT" != "$ROOT/vault" ]; then
  echo "export GENESIS_VAULT=\"$VAULT\"" | $SUDO tee /etc/profile.d/genesis.sh >/dev/null
  echo "Saved GENESIS_VAULT in /etc/profile.d/genesis.sh (new shells pick it up)."
fi

say "This machine"
GENESIS_VAULT="$VAULT" "$ROOT/bin/genesis" doctor || true

cat <<EOF

Genesis is installed. Next:

  genesis plan --tier seed        see what the essentials cost in disk space
  genesis fetch --tier seed       download and verify them
  genesis up core                 start the dashboard at http://localhost:8080
  genesis timer                   update everything once a month, automatically

EOF
