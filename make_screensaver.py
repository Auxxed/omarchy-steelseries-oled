#!/usr/bin/python3 -I
"""Build the "Screensaver" OLED loops from Omarchy's idle screensaver.

Runs `ttfx` (the terminal text effects engine behind omarchy-screensaver) on
the screensaver logo with a canvas sized to the logo, parses the frames it
paints, and rasterises each character cell onto the 128x40 1-bit panel.
Writes one assets/screensaver/<effect>.frames (+ .gif preview) per effect.
Building the logo loops needs ttfx and ImageMagick; the plugin runtime just
plays the resulting .frames files like any other bundled loop.

apply.py also runs these effects on custom text at runtime: bitmap_art()
turns a 128x40 bitmap into half-block art, one cell per pixel column and two
pixel rows, and effect_frames() animates it the same way.

    ./make_screensaver.py [-i screensaver.txt] [effect ...]
"""
from __future__ import annotations

import os
import re
import select
import subprocess
import sys
import time

TTFX = "/usr/bin/ttfx"

from make_gif import ASSETS, H, W, blank, pack, write_frames_blob, write_gif

DEFAULT_INPUT = os.path.expanduser("~/.config/omarchy/branding/screensaver.txt")
OUT_DIR = os.path.join(ASSETS, "screensaver")
# Every ttfx effect. Keep in sync with screensaverEffects in Service.qml.
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
# The effects ttfx times in seconds. Every other one is timed in frames and
# paints the same frames uncapped (--frame-rate 0), only much faster.
TIMED_EFFECTS = {"matrix", "thunderstorm"}
CAPTURE_TIMEOUT_S = 60
# Far above any real capture (~25 MB); stops a runaway effect eating memory.
MAX_CAPTURE_BYTES = 256 * 1024 * 1024
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
BLOCKS = {halves: ch for ch, halves in HALVES.items()}
# Line-drawing glyphs (synthgrid's grid, beams) drawn as real 1px lines;
# junctions sit in both sets.
HLINE = set("─━═-_┌┐└┘├┤┬┴┼╭╮╯╰")
VLINE = set("│┃║|¦┌┐└┘├┤┬┴┼╭╮╯╰")


def read_logo(path: str) -> tuple[int, int]:
    lines = open(path, encoding="utf-8").read().rstrip("\n").split("\n")
    return max(len(line) for line in lines), len(lines)


def bitmap_art(pix: list[list[int]]) -> tuple[str, int]:
    """A W x H bitmap as half-block art (W columns, H/2 rows) and the row
    its first inked line sits on. ttfx drops blank lines around its input,
    so the art starts at that row and the caller offsets the canvas by it."""
    lines = ["".join(BLOCKS[(pix[y][x], pix[y + 1][x])] for x in range(W)).rstrip() for y in range(0, H, 2)]
    inked = [i for i, line in enumerate(lines) if line]
    if not inked:
        raise SystemExit("nothing to animate")
    top = inked[0]
    return "\n".join(lines[top : inked[-1] + 1]) + "\n", top


def capture(effect: str, art: str, cols: int, rows: int) -> bytes:
    """Everything ttfx paints for one effect on a cols x rows canvas with
    the art in its top left corner. No terminal is involved."""
    fps = SOURCE_FPS if effect in TIMED_EFFECTS else 0
    proc = subprocess.Popen(
        [
            TTFX, "-i", art,
            "--frame-rate", str(fps),
            "--canvas-width", str(cols), "--canvas-height", str(rows),
            "--anchor-canvas", "nw", "--anchor-text", "nw",
            "--ignore-terminal-dimensions", "--no-eol", "--no-restore-cursor",
            effect,
            *EFFECT_ARGS.get(effect, []),
        ],
        stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
    )
    out = bytearray()
    deadline = time.monotonic() + CAPTURE_TIMEOUT_S
    finished = False
    try:
        while True:
            left = deadline - time.monotonic()
            if left <= 0:
                raise SystemExit(f"ttfx {effect} still running after {CAPTURE_TIMEOUT_S}s")
            if not select.select([proc.stdout], [], [], left)[0]:
                continue
            chunk = os.read(proc.stdout.fileno(), 1 << 20)
            if not chunk:
                break
            out += chunk
            if len(out) > MAX_CAPTURE_BYTES:
                raise SystemExit(f"ttfx {effect} painted too much")
        finished = True
    finally:
        if not finished:
            proc.kill()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
        proc.stdout.close()
    if proc.returncode != 0:
        raise SystemExit(f"ttfx {effect} failed (exit {proc.returncode})")
    return bytes(out)


def split_frames(raw: bytes) -> list[bytes]:
    """ttfx output, one chunk per painted frame: each frame starts after the
    cursor-up that rewinds to the top of the canvas."""
    return CUP_UP.split(raw)[2::2]


def parse_frame(chunk: bytes, rows: int) -> list[list[tuple[str, float]]]:
    """One painted frame as rows x cells of (char, luma)."""
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
    return grid[:rows]


def cell_span(i: int, n: int, size: int) -> tuple[int, int]:
    return (i * size // n, (i + 1) * size // n)


def glyph_bits(ch: str) -> int:
    # Stable 4-bit sparkle for non-block glyphs (decrypt noise, matrix rain…),
    # so the same character keeps the same shape across frames.
    h = (ord(ch) * 2654435761) & 0xFFFFFFFF
    bits = (h >> 13) & 0xF
    return bits or 0b0110


def rasterise(grid: list[list[tuple[str, float]]], cols: int, rows: int, top: int = 0) -> bytes:
    """Paint a parsed frame whose first line is cell row `top` of a
    cols x rows grid laid over the panel."""
    pix = blank()
    for cy, line in enumerate(grid[: rows - top], start=top):
        y0, y1 = cell_span(cy, rows, H)
        ymid = (y0 + y1) // 2
        for cx, (ch, luma) in enumerate(line[:cols]):
            if ch == " " or luma < LUMA_ON:
                continue
            x0, x1 = cell_span(cx, cols, W)
            if ch in HALVES:
                top_half, bottom_half = HALVES[ch]
                spans = ([range(y0, ymid)] if top_half else []) + ([range(ymid, y1)] if bottom_half else [])
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
                # A cell only two pixels tall can miss every set bit, and
                # a glyph should never vanish: light its top pixel then.
                bits = glyph_bits(ch)
                lit = [y for k, y in enumerate(range(y0, y1)) if bits >> (k % 4) & 1] or [y0]
                for y in lit:
                    pix[y][x0] = 1
    return pack(pix)


def resample_indices(count: int) -> list[int]:
    """Which of `count` captured frames to keep at DELAY_MS."""
    step = (1000.0 / DELAY_MS) / SOURCE_FPS
    n = max(1, min(int(count * step), int(MAX_EFFECT_S * 1000 / DELAY_MS)))
    last = count - 1
    return [round(i * last / max(1, n - 1)) for i in range(n)]


def effect_frames(
    effect: str, art: str, cols: int, rows: int, top: int = 0, final: bytes | None = None
) -> list[bytes]:
    """One effect's loop: the art file (its first line on cell row `top`)
    animated on a cols x rows grid, resampled to DELAY_MS, then the result
    held. Many effects settle on a gradient whose dark end falls under
    LUMA_ON, so `final` (the art's own bitmap) is held instead when given.
    Only the frames that are kept get parsed."""
    chunks = split_frames(capture(effect, art, cols, rows - top))
    if not chunks:
        raise SystemExit(f"ttfx {effect}: no frames parsed")
    frames = [rasterise(parse_frame(chunks[i], rows - top), cols, rows, top) for i in resample_indices(len(chunks))]
    frames.extend(hold_last(final or frames[-1]))
    return frames


def main() -> None:
    args = sys.argv[1:]
    logo = DEFAULT_INPUT
    if args[:1] == ["-i"]:
        logo, args = args[1], args[2:]
    effects = args or EFFECTS
    cols, rows = read_logo(logo)
    os.makedirs(OUT_DIR, exist_ok=True)
    for effect in effects:
        frames = effect_frames(effect, logo, cols, rows)
        base = os.path.join(OUT_DIR, effect)
        write_frames_blob(frames, base + ".frames", DELAY_MS)
        write_gif(frames, base + ".gif", DELAY_MS)
        print(f"{effect}: {len(frames)} frames", flush=True)


def hold_last(frame: bytes) -> list[bytes]:
    return [frame] * int(HOLD_S * 1000 / DELAY_MS)


if __name__ == "__main__":
    main()
