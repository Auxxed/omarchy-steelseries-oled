#!/usr/bin/env bash
# Remove the hidraw permission rule this plugin installed.
# Run as root: sudo ./uninstall-udev.sh
set -euo pipefail

if [[ ${EUID} -ne 0 ]]; then
  echo "Run as root: sudo $0" >&2
  exit 1
fi

DEST="/etc/udev/rules.d/99-steelseries-apex-oled.rules"
if [[ -f $DEST ]]; then
  rm -f "$DEST"
  udevadm control --reload-rules
  echo "Removed $DEST"
else
  echo "No rule at $DEST"
fi
