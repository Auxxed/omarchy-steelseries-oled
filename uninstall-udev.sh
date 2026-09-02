#!/usr/bin/env bash
# Remove the hidraw permission rule this plugin installed.
# Run as root: sudo ./uninstall-udev.sh
set -euo pipefail

if [[ ${EUID} -ne 0 ]]; then
  echo "Run as root: sudo $0" >&2
  exit 1
fi

REMOVED=0
for dest in \
  /etc/udev/rules.d/71-steelseries-apex-oled.rules \
  /etc/udev/rules.d/99-steelseries-apex-oled.rules \
  /etc/udev/rules.d/99-steelseries-apex7-oled.rules
do
  if [[ -f $dest ]]; then
    rm -f "$dest"
    echo "Removed $dest"
    REMOVED=1
  fi
done
if [[ $REMOVED -eq 1 ]]; then
  udevadm control --reload-rules
else
  echo "No SteelSeries OLED udev rule installed"
fi
