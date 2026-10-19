#!/usr/bin/env bash
# One-time: install hidraw permissions for SteelSeries Apex OLED keyboards.
# Run as root: sudo ./install-udev.sh
#
# The rule bytes are embedded below rather than read from
# udev/71-steelseries-apex-oled.rules in this checkout: that file lives in a
# directory the invoking user (and so any other same-UID process) can still
# write to for as long as this script runs as root, which would otherwise be
# a window to swap in a rule with a RUN+= directive before udevadm trigger
# fires it. Keep this block byte-identical to that file — CI checks it.
set -euo pipefail

if [[ ${EUID} -ne 0 ]]; then
  echo "Run as root: sudo $0" >&2
  exit 1
fi

DEST="/etc/udev/rules.d/71-steelseries-apex-oled.rules"
TMP="$(mktemp /etc/udev/rules.d/.71-steelseries-apex-oled.rules.XXXXXXXXXX)"
trap 'rm -f -- "$TMP"' EXIT

cat >"$TMP" <<'RULE'
# SteelSeries Apex OLED (Pro / 7 / 5, full-size and TKL).
# TAG+="uaccess" lets the seated user write the vendor hidraw node without
# being in the input group. This file is named 71- so 73-seat-late.rules
# still applies the ACL; a 99- rule is too late and access never lands.
# This rule does not run any program.
KERNEL=="hidraw*", SUBSYSTEM=="hidraw", ATTRS{idVendor}=="1038", ATTRS{idProduct}=="1610|1612|1614|1618|161c", MODE="0660", GROUP="input", TAG+="uaccess"
RULE
chmod 644 "$TMP"
mv -f -T -- "$TMP" "$DEST"

# Previous names: 99- is too late for TAG+="uaccess"; the apex7 RUN-on-add
# rule fights the plugin watcher and does not grant the seated user access.
rm -f /etc/udev/rules.d/99-steelseries-apex-oled.rules \
      /etc/udev/rules.d/99-steelseries-apex7-oled.rules
udevadm control --reload-rules
# Re-apply the rule to the keyboard's own hidraw nodes and nothing else. A
# blanket trigger over every hidraw node re-announces unrelated devices, and
# a game running under Proton lost its Steam Controller that way. A change
# event is enough: 73-seat-late.rules grants uaccess on every action but
# remove.
for node in /sys/class/hidraw/hidraw*; do
  [[ -r $node/device/uevent ]] || continue
  case $(grep -m1 '^HID_ID=' "$node/device/uevent" || true) in
  HID_ID=0003:00001038:00001610 | HID_ID=0003:00001038:00001612 | HID_ID=0003:00001038:00001614 | \
    HID_ID=0003:00001038:00001618 | HID_ID=0003:00001038:0000161C)
    udevadm trigger --action=change -- "$node"
    ;;
  esac
done
echo "Installed $DEST"
