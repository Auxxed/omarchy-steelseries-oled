#!/usr/bin/env bash
# One-time: install hidraw permissions for SteelSeries Apex OLED keyboards.
# Run as root: sudo ./install-udev.sh
set -euo pipefail

if [[ ${EUID} -ne 0 ]]; then
  echo "Run as root: sudo $0" >&2
  exit 1
fi

HERE="$(cd "$(dirname "$0")" && pwd)"
SRC="$HERE/udev/71-steelseries-apex-oled.rules"
DEST="/etc/udev/rules.d/71-steelseries-apex-oled.rules"

install -m 644 "$SRC" "$DEST"
# Previous names: 99- is too late for TAG+="uaccess"; the apex7 RUN-on-add
# rule fights the plugin watcher and does not grant the seated user access.
rm -f /etc/udev/rules.d/99-steelseries-apex-oled.rules \
      /etc/udev/rules.d/99-steelseries-apex7-oled.rules
udevadm control --reload-rules
udevadm trigger --action=add --subsystem-match=hidraw
udevadm trigger --action=change --subsystem-match=hidraw
echo "Installed $DEST"
