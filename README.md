# SteelSeries OLED

Omarchy plugin that puts the official **OMARCHY** wordmark on the OLED of a
SteelSeries Apex keyboard.

A keyboard icon in the bar shows whether the GIF is looping. Click it for
status and a one-time udev install (polkit prompt). The service keeps
streaming after you unplug and replug.

![Omarchy wordmark on a 128×40 Apex OLED](preview.png)

The Apex OLED has no onboard animation storage — frames are streamed over
HID — so the onboard OLED menus will lose the fight while the service is
running. Use `apply.py --once` for a still wordmark if you want those menus
back.

## Supported keyboards

Same 128×40 legacy OLED protocol:

- Apex 7 and Apex 7 TKL (`1038:1612`, `1038:1618`)
- Apex Pro and Apex Pro TKL (`1038:1610`, `1038:1614`)
- Apex 5 (`1038:161c`)

## Install

```sh
omarchy plugin add https://github.com/Auxxed/omarchy-steelseries-oled.git --enable
```

The widget lands on the right of the bar. Move it with:

```sh
omarchy bar move io.github.auxxed.steelseries-oled --section center
```

Runtime is `python3` (stdlib) for HID. Importing a custom GIF or still needs ImageMagick (`magick`), which Omarchy already ships.

### Keyboard access

The OLED is `/dev/hidraw*` on USB vendor `1038`, interface 1. A one-time udev
rule lets the seated session write it without root. The panel can install that
rule through a polkit prompt (fixed bytes, not a copy of the plugin tree). By
hand:

```sh
sudo ~/.config/omarchy/plugins/io.github.auxxed.steelseries-oled/install-udev.sh
```

The rule is named `71-steelseries-apex-oled.rules` so `TAG+="uaccess"` is
applied before systemd seat ACLs. It sets `MODE=0660` plus `uaccess` for the
listed Apex PIDs. It does not run any program. Unplug and replug if the ACL
does not land immediately.

## Usage

- Left click the bar icon: open the panel
- **Display** (or right-click the icon): turn the loop on or off
- **Invert**: flip black and white
- **Speed**: frame delay
- **Contrast**: 1-bit threshold for a custom image
- **Choose image** imports a GIF or still (png/jpg/webp/bmp), resized to 128×40 1-bit. **Use Omarchy** restores the bundled wordmark without deleting the last import; **Use last image** brings it back.
- **Allow access** appears only when the keyboard is present but not writable
- `omarchy-shell io.github.auxxed.steelseries-oled power`

## Remove

```sh
omarchy plugin remove io.github.auxxed.steelseries-oled
```

If the plugin directory is already gone, delete the rule by hand:

```sh
sudo rm -f /etc/udev/rules.d/71-steelseries-apex-oled.rules
sudo udevadm control --reload-rules
```

Removal does not factory-reset the OLED. Unplug the keyboard (or open its
onboard menu) to leave the firmware logo showing again. The udev rule, if you
installed it, stays until you remove that file yourself.

## Manual apply

```sh
# Loop the GIF in the foreground (Ctrl-C leaves the still wordmark)
python3 ~/.config/omarchy/plugins/io.github.auxxed.steelseries-oled/apply.py

# Still wordmark only
python3 ~/.config/omarchy/plugins/io.github.auxxed.steelseries-oled/apply.py --once
python3 ~/.config/omarchy/plugins/io.github.auxxed.steelseries-oled/apply.py --invert
```

`assets/omarchy-oled-128x40.gif` is the looping idle animation. Regenerate it
from the still wordmark with `python3 make_gif.py` (needs ImageMagick). The
`.png` is the same still, sized for SteelSeries GG if you ever set a static
idle screen from Windows.

Plugin files: `manifest.json`, `Service.qml`, `OledBarWidget.qml`, `OledPanel.qml`,
`apply.py`, `udev/71-steelseries-apex-oled.rules`.

## How it works

A headless `service` starts `apply.py --watch`. That process finds USB vendor
`1038` on HID interface 1, then streams 642-byte feature reports (`0x61` + 640
packed pixels) at 10 fps — the same payload Linux Apex 7 tools have used for
years. JSON status lines drive the bar widget.

The idle image lives in keyboard RAM. Firmware restores its own logo after a
power cycle unless this service is running.

## Marketplace

Category **Hardware**. Tags: `bar`, `quickshell`, `system`.

## License

MIT. See [LICENSE](LICENSE).

The wordmark is the official Omarchy mark from `logo.svg` (MIT), rasterized to
the Apex OLED's 128×40 1-bit panel. Omarchy and SteelSeries names are used to
describe the hardware and desktop this plugin talks to.
