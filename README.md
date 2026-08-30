# SteelSeries OLED

Omarchy shell plugin that puts the official **OMARCHY** wordmark on the
OLED of a SteelSeries Apex keyboard.

![Omarchy wordmark on a 128×40 Apex OLED](preview.png)

When the plugin is enabled it watches for the keyboard, writes a 128×40
black-and-white image to the idle screen, and writes it again after you
unplug and replug. Using the keyboard's own OLED menus still works; the
wordmark comes back the next time the device appears.

## Supported keyboards

Same 128×40 legacy OLED protocol:

- Apex 7 and Apex 7 TKL (`1038:1612`, `1038:1618`)
- Apex Pro and Apex Pro TKL (`1038:1610`, `1038:1614`)
- Apex 5 (`1038:161c`)

## Requirements

- Omarchy (Quattro / `omarchy plugin add`)
- Python 3 (stdlib only)
- Membership in the `input` group (Omarchy's default user has this)
- A one-time udev rule so hidraw is writable without root (see below)

## Install

```sh
omarchy plugin add https://github.com/auxxed/omarchy-steelseries-oled.git --enable
```

Then install the permission rule once:

```sh
sudo ~/.config/omarchy/plugins/io.github.auxxed.steelseries-oled/install-udev.sh
```

Unplug and replug the keyboard, or the script already triggers hidraw.
After that, `omarchy restart shell` if the wordmark is not up yet.

The udev rule only sets `MODE=0660` and `GROUP=input`. It does not run
any program.

## Remove

```sh
sudo ~/.config/omarchy/plugins/io.github.auxxed.steelseries-oled/uninstall-udev.sh
omarchy plugin remove io.github.auxxed.steelseries-oled
```

If the plugin directory is already gone, delete the rule by hand:

```sh
sudo rm -f /etc/udev/rules.d/99-steelseries-apex-oled.rules
sudo udevadm control --reload-rules
```

Removal does not factory-reset the OLED. Unplug the keyboard (or open
its onboard menu) to leave the firmware logo showing again.

## Manual apply

```sh
python3 ~/.config/omarchy/plugins/io.github.auxxed.steelseries-oled/apply.py
python3 ~/.config/omarchy/plugins/io.github.auxxed.steelseries-oled/apply.py --invert
```

`assets/omarchy-oled-128x40.png` and `.gif` are the same image, sized
for SteelSeries GG if you ever set the idle screen from Windows.

## How it works

A headless `service` plugin starts `apply.py --watch`. That process
finds USB vendor `1038` on HID interface 1, then sends a 642-byte
feature report (`0x61` + 640 packed pixels) — the same payload Linux
Apex 7 tools have used for years.

The idle image lives in keyboard RAM. Firmware restores its own logo
after a power cycle unless this service is running.

## Publish

This is a single-root Omarchy plugin (`manifest.json` at the repo root).
List it at [omarchyplugins.com](https://omarchyplugins.com/publish.html):

1. Push this repository to GitHub (public).
2. Run `omarchy plugin validate .` on a checkout.
3. Open a `[Plugin]: SteelSeries OLED` issue on
   [HANCORE-linux/omarchy-plugin-marketplace](https://github.com/HANCORE-linux/omarchy-plugin-marketplace/issues/new?template=submit-plugin.yml)
   with category `Hardware` and tags `system, quickshell`.

## License

MIT. See [LICENSE](LICENSE).

The wordmark is the official Omarchy mark from `logo.svg` (MIT), rasterized
to the Apex OLED's 128×40 1-bit panel. Omarchy and SteelSeries names are
used to describe the hardware and desktop this plugin talks to.
