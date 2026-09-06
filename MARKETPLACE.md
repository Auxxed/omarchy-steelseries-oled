# Marketplace submission

Open the issue form:

https://github.com/omacom/omarchy-plugin-marketplace/issues/new?template=submit-plugin.yml

Fill it as follows. The repository must be **public** at the URL below
before you submit — marketplace validation clones that commit.

## Title

`[Plugin]: SteelSeries OLED`

## Repository URL

https://github.com/Auxxed/omarchy-steelseries-oled

## Category

Hardware

## Tags

Bar, Quickshell, System

(one to three; more than three is rejected)

## Suggest a missing tag

keyboard

## Maintainer notes

Please validate repository HEAD after push.

Headless service + bar widget (`io.github.auxxed.steelseries-oled`) for
SteelSeries Apex OLED keyboards (Apex 7 / Pro / 5, 128×40, USB `1038:1610|1612|1614|1618|161c`).

- HID runtime is `python3` from the Omarchy install (stdlib only) and never
  touches the network. The bar panel's "grab a gif" button is the one
  network path in the plugin: an on-demand HTTPS GET to a two-name host
  allowlist (`nlog.us`/`www.nlog.us`), triggered only by that click, capped
  at 40 MiB and 15s. Everything else — the bundled wordmark cycle, custom
  image import, typed text — is local.
- Bundled art: Omarchy typewriter, still wordmark, 3D spin. Custom GIF/still
  import uses ImageMagick (`magick`), already on Omarchy.
- Display off blanks the OLED instead of leaving the last frame.
- Optional one-time udev rule: hidraw for those PIDs only, `MODE=0660` plus
  `TAG+="uaccess"`, file named `71-steelseries-apex-oled.rules` so seat ACLs
  still apply. The rule does not run any program.
- The panel offers a polkit install of that rule: `pkexec` runs `python3 -c`
  with the exact rule bytes in argv (frozen at spawn), writes
  `/etc/udev/rules.d/71-steelseries-apex-oled.rules` atomically, then
  `udevadm reload` + hidraw trigger. The plugin tree is never opened as root.
- Without the keyboard the bar icon still runs and reports "No keyboard".
- Install: `omarchy plugin add https://github.com/Auxxed/omarchy-steelseries-oled.git --enable`

## Submission checklist

- [x] The repository is public and contains installation and removal instructions.
- [x] I have documented the plugin license and any external dependencies.
- [x] I confirm that I own or have permission to submit this plugin and its preview assets.
- [x] The plugin does not overwrite user configuration without explicit consent.
- [x] I understand that approval is for listing and is not a security review.
