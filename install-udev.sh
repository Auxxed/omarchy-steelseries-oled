#!/usr/bin/env bash
# One-time: install hidraw permissions for SteelSeries Apex OLED keyboards.
# Run as root: sudo ./install-udev.sh
set -euo pipefail

if [[ ${EUID} -ne 0 ]]; then
  echo "Run as root: sudo $0" >&2
  exit 1
fi

HERE="$(cd "$(dirname "$0")" && pwd)"
SRC="$HERE/udev/99-steelseries-apex-oled.rules"
DEST="/etc/udev/rules.d/99-steelseries-apex-oled.rules"

install -m 644 "$SRC" "$DEST"
udevadm control --reload-rules
udevadm trigger --action=add --subsystem-match=hidraw
echo "Installed $DEST"
