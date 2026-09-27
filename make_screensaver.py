#!/usr/bin/env python3
"""Build the "Screensaver" OLED loops from Omarchy's idle screensaver.

Runs `ttfx` (the terminal text effects engine behind omarchy-screensaver) on
the screensaver logo inside a pseudo-terminal sized to the logo, parses the
frames it paints, and rasterises each character cell onto the 128x40 1-bit
panel. Writes one assets/screensaver/<effect>.frames (+ .gif preview) per
effect. Build-time only: needs ttfx and ImageMagick; the plugin runtime just
plays the resulting .frames files like any other bundled loop.

    ./make_screensaver.py [-i screensaver.txt] [effect ...]
"""
from __future__ import annotations

import fcntl
import os
import pty
import re
import select
import signal
import struct
import sys
import termios
import time

from make_gif import ASSETS, H, W, blank, pack, write_frames_blob, write_gif

DEFAULT_INPUT = os.path.expanduser("~/.config/omarchy/branding/screensaver.txt")
OUT_DIR = os.path.join(ASSETS, "screensaver")
# Every ttfx 0.3 effect. Keep in sync with screensaverEffects in Service.qml.
EFFECTS = [
    "beams", "binarypath", "blackhole", "bouncyballs", "bubbles", "burn",
    "colorshift", "crumble", "decrypt", "errorcorrect", "expand", "fireworks",
    "highlight", "laseretch", "matrix", "middleout", "orbittingvolley",
    "overflow", "pour", "print", "rain", "randomsequence", "rings", "scattered",
    "slice", "slide", "smoke", "spotlights", "spray", "swarm", "sweep",
    "synthgrid", "thunderstorm", "unstable", "vhstape", "waves", "wipe",
]
# Extra ttfx flags per effect. Matrix and thunderstorm run for wall-clock
# seconds before resolving the logo; the screensaver's defaults are too long
# for a loop. Colorshift would otherwise cycle forever.
EFFECT_ARGS = {
    "matrix": ["--rain-time", "5"],
    "thunderstorm": ["--storm-time", "5"],
    "colorshift": ["--no-loop", "--cycles", "2"],
}
CAPTURE_TIMEOUT_S = 60
DELAY_MS = 60
# ttfx runs at 120 fps in the real screensaver. Capture at the same rate (some
# effects are timed in seconds, not frames) and keep each effect's wall-clock
# length when resampling to DELAY_MS, capped so one slow effect can't hog the
# loop.
SOURCE_FPS = 120
MAX_EFFECT_S = 9.0
HOLD_S = 1.5
# Cells dimmer than this (0-1, perceived luma of the 24-bit fg colour) stay off.
LUMA_ON = 0.30

CUP_UP = re.compile(rb"\x1b\[(\d+)A")
TOKEN = re.compile(r"\x1b\[([0-9;]*)m|\x1b[78]|\x1b\[\?\d+[lh]|(.)", re.S)

# Half-block art: which of the cell's two vertical halves are inked.
HALVES = {" ": (0, 0), "▀": (1, 0), "▄": (0, 1), "█": (1, 1)}
# Line-drawing glyphs (synthgrid's grid, beams) drawn as real 1px lines;
# junctions sit in both sets.
HLINE = set("─━═-_┌┐└┘├┤┬┴┼╭╮╯╰")
VLINE = set("│┃║|¦┌┐└┘├┤┬┴┼╭╮╯╰")


def read_logo(path: str) -> tuple[int, int]:
    lines = open(path, encoding="utf-8").read().rstrip("\n").split("\n")
    return max(len(line) for line in lines), len(lines)


def capture(effect: str, logo: str, cols: int, rows: int) -> bytes:
    pid, fd = pty.fork()
    if pid == 0:
        os.execvp(
            "ttfx",
            [
                "ttfx", "-i", logo,
                "--frame-rate", str(SOURCE_FPS),
                "--canvas-width", "0", "--canvas-height", "0",
                "--anchor-canvas", "c", "--anchor-text", "c",
                "--no-eol", "--no-restore-cursor",
                effect,
                *EFFECT_ARGS.get(effect, []),
            ],
        )
    fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))
    out = bytearray()
    deadline = time.monotonic() + CAPTURE_TIMEOUT_S
    while True:
        left = deadline - time.monotonic()
        if left <= 0:
            os.kill(pid, signal.SIGKILL)
            os.waitpid(pid, 0)
            raise SystemExit(f"ttfx {effect} still running after {CAPTURE_TIMEOUT_S}s")
        if not select.select([fd], [], [], left)[0]:
            continue
        try:
            chunk = os.read(fd, 1 << 16)
        except OSError:
            break
        if not chunk:
            break
        out += chunk
    os.close(fd)
    _, status = os.waitpid(pid, 0)
    if os.waitstatus_to_exitcode(status) != 0:
        raise SystemExit(f"ttfx {effect} failed:\n{out[-400:].decode(errors='replace')}")
    return bytes(out)


def parse_frames(raw: bytes, rows: int) -> list[list[list[tuple[str, float]]]]:
    """Split ttfx output into frames of rows x cells of (char, luma)."""
    frames = []
    for chunk in CUP_UP.split(raw)[2::2]:
        text = chunk.decode("utf-8", errors="replace")
        grid: list[list[tuple[str, float]]] = [[]]
        luma = 1.0
        for m in TOKEN.finditer(text):
            sgr, ch = m.group(1), m.group(2)
            if sgr is not None:
                parts = sgr.split(";")
                if parts[:2] == ["38", "2"] and len(parts) >= 5:
                    r, g, b = (int(p) for p in parts[2:5])
                    luma = (0.2126 * r + 0.7152 * g + 0.0722 * b) / 255.0
                elif parts in (["0"], [""]):
                    luma = 1.0
            elif ch == "\n":
                grid.append([])
            elif ch is not None and ch != "\r":
                grid[-1].append((ch, luma))
        frames.append(grid[:rows])
    return frames


def cell_span(i: int, n: int, size: int) -> tuple[int, int]:
    return (i * size // n, (i + 1) * size // n)


def glyph_bits(ch: str) -> int:
    # Stable 4-bit sparkle for non-block glyphs (decrypt noise, matrix rain…),
    # so the same character keeps the same shape across frames.
    h = (ord(ch) * 2654435761) & 0xFFFFFFFF
    bits = (h >> 13) & 0xF
    return bits or 0b0110


def rasterise(grid: list[list[tuple[str, float]]], cols: int, rows: int) -> bytes:
    pix = blank()
    for cy, line in enumerate(grid):
        y0, y1 = cell_span(cy, rows, H)
        ymid = (y0 + y1) // 2
        for cx, (ch, luma) in enumerate(line[:cols]):
            if ch == " " or luma < LUMA_ON:
                continue
            x0, x1 = cell_span(cx, cols, W)
            if ch in HALVES:
                top, bottom = HALVES[ch]
                spans = ([range(y0, ymid)] if top else []) + ([range(ymid, y1)] if bottom else [])
                for ys in spans:
                    for y in ys:
                        for x in range(x0, x1):
                            pix[y][x] = 1
            elif ch in HLINE or ch in VLINE:
                if ch in HLINE:
                    for x in range(x0, x1):
                        pix[ymid][x] = 1
                if ch in VLINE:
                    for y in range(y0, y1):
                        pix[y][x0] = 1
            else:
                bits = glyph_bits(ch)
                for k, y in enumerate(range(y0, y1)):
                    if bits >> (k % 4) & 1:
                        pix[y][x0] = 1
    return pack(pix)


def resample(frames: list[bytes]) -> list[bytes]:
    step = (1000.0 / DELAY_MS) / SOURCE_FPS
    n = max(1, min(int(len(frames) * step), int(MAX_EFFECT_S * 1000 / DELAY_MS)))
    last = len(frames) - 1
    return [frames[round(i * last / max(1, n - 1))] for i in range(n)]


def main() -> None:
    args = sys.argv[1:]
    logo = DEFAULT_INPUT
    if args[:1] == ["-i"]:
        logo, args = args[1], args[2:]
    effects = args or EFFECTS
    cols, rows = read_logo(logo)
    os.makedirs(OUT_DIR, exist_ok=True)
    for effect in effects:
        grids = parse_frames(capture(effect, logo, cols, rows), rows)
        if not grids:
            raise SystemExit(f"ttfx {effect}: no frames parsed")
        frames = resample([rasterise(g, cols, rows) for g in grids])
        frames.extend(hold_last(frames[-1]))
        base = os.path.join(OUT_DIR, effect)
        write_frames_blob(frames, base + ".frames", DELAY_MS)
        write_gif(frames, base + ".gif", DELAY_MS)
        print(f"{effect}: {len(grids)} ttfx frames -> {len(frames)}", flush=True)


def hold_last(frame: bytes) -> list[bytes]:
    return [frame] * int(HOLD_S * 1000 / DELAY_MS)


if __name__ == "__main__":
    main()
