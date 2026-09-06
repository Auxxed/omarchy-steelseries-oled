# SteelSeries OLED

Omarchy plugin for the OLED on a SteelSeries Apex keyboard: the official
**OMARCHY** wordmark by default, or a 3D spin/waves/static cycle of it, a
custom GIF or still image, a random pull from a bundled or online gif
library, or your own typed text — rendered in JetBrains Mono or in
**Omarchy Block**, a companion display font built for this plugin from the
wordmark's own letterforms.

A keyboard icon in the bar shows whether the GIF is looping. Click it for
status and a one-time udev install (polkit prompt). The service keeps
streaming after you unplug and replug.

![Omarchy wordmark on a 128×40 Apex OLED](preview.png)

The Apex OLED has no onboard animation storage — frames are streamed over
HID — so the onboard OLED menus will lose the fight while the service is
running. Turning Display off (or `apply.py --release`) blanks the panel.

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
- **Display** (or right-click the icon): turn the loop on or off. Off blanks the panel.
- **Invert**: flip black and white (the panel preview follows)
- **Speed**: frame delay
- **Contrast**: 1-bit threshold for a custom image
- **Omarchy Logo** cycles the bundled wordmark art: typewriter loop → still → 3D spin → waves
- **Choose image** imports a GIF or still (png/jpg/webp/bmp), resized to 128×40 1-bit. **Use last image** brings a custom import back.
- The **#** button grabs a random OLED gif — Stick Fight and Night Runner ship
  with the plugin, the rest are fetched on demand from nlog.us. Right-click
  to search and pick one by name instead of cycling.
- Type your own text, then pick a **font** (JetBrains Mono for full
  character coverage, or Omarchy Block to match the logo) and a **style**
  (typewriter/static/spin/waves) — click either button to cycle, right-click
  to pick from a list. **Use text** switches the OLED to it.
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
# Loop the GIF in the foreground (Ctrl-C blanks the panel)
python3 ~/.config/omarchy/plugins/io.github.auxxed.steelseries-oled/apply.py

# Still wordmark only
python3 ~/.config/omarchy/plugins/io.github.auxxed.steelseries-oled/apply.py --once
python3 ~/.config/omarchy/plugins/io.github.auxxed.steelseries-oled/apply.py --invert

# Blank the panel
python3 ~/.config/omarchy/plugins/io.github.auxxed.steelseries-oled/apply.py --release

# Render typed text (style: typewriter/static/spin/waves, font: jetbrains/omarchy)
python3 ~/.config/omarchy/plugins/io.github.auxxed.steelseries-oled/apply.py \
  --render-text "HYPR" --style spin --font omarchy \
  --out-dir ~/.local/state/omarchy/steelseries-oled
```

`assets/omarchy-oled-128x40.gif` is the looping idle animation, with
`omarchy-oled-static.frames` and `omarchy-oled-spin.frames` and
`omarchy-oled-waves.frames` alongside it for the other bundled cycle steps.
Regenerate all of them from the still wordmark with `python3 make_gif.py`
(needs ImageMagick). The `.png` is the same still, sized for SteelSeries GG
if you ever set a static idle screen from Windows.

Plugin files: `manifest.json`, `Service.qml`, `OledBarWidget.qml`, `OledPanel.qml`,
`invert.frag`, `apply.py`, `make_gif.py`, `udev/71-steelseries-apex-oled.rules`.

## How it works

A headless `service` starts `apply.py --watch`. That process finds USB vendor
`1038` on HID interface 1, then streams 642-byte feature reports (`0x61` + 640
packed pixels) at 10 fps — the same payload Linux Apex 7 tools have used for
years. JSON status lines drive the bar widget.

The idle image lives in keyboard RAM. Firmware restores its own logo after a
power cycle unless this service is running.

Typed text goes through the same pipeline as a custom image: ImageMagick
rasterizes it in the chosen font, thresholds it to 1-bit at 128×40, and
`make_gif.py` turns that bitmap into an animation using the same per-letter
segmentation and spin/wave math it uses for the bundled wordmark — just
parameterized over however many letters were actually typed instead of a
fixed "OMARCHY".

## Marketplace

Category **Hardware**. Tags: `bar`, `quickshell`, `system`.

## License

MIT. See [LICENSE](LICENSE).

The wordmark is the official Omarchy mark from `logo.svg` (MIT), rasterized to
the Apex OLED's 128×40 1-bit panel. Omarchy and SteelSeries names are used to
describe the hardware and desktop this plugin talks to.

`assets/fonts/OmarchyBlock-Regular.ttf` ("Omarchy Block") is a companion
display font built for this plugin: it traces the real wordmark's 15-unit
grid letterforms from `logo.svg` for O/M/A/R/C/H/Y, then extends that same
chamfered-block, staircase-diagonal construction to the rest of A–Z and
0–9, so short custom-text lockups can match the logo's style. It's a
caps-only display face — lowercase maps to the same outlines, and it has no
extended punctuation — which is why JetBrains Mono stays the default font
for typed text.

`assets/stickfight.gif` is SteelSeries' own "Stick Fight" OLED gif from their
[OLED customization blog post](https://steelseries.com/blog/steelseries-oled-gifs-and-customization-137).
`assets/nightrunner.gif` is the "Night Runner" gif from
[this r/steelseries post](https://www.reddit.com/r/steelseries/comments/zou9mm/heres_a_gif_for_the_oled_screen_featuring_michiru/).
Both ship as bundled picks in the "grab a gif" cycle; the rest come from
[nlog.us's SteelSeries OLED gif page](https://www.nlog.us/ps/steelseries_oled_gifs.html)
and are fetched on demand rather than bundled.
