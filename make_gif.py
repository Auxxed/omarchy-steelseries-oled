#!/usr/bin/env python3
"""Build the looping 128x40 1-bit Omarchy OLED GIF from the static wordmark.

Stdlib for the frame data. ImageMagick (`magick`) writes the .gif preview.
"""
from __future__ import annotations

import os
import struct
import subprocess
import tempfile

W, H = 128, 40
PAYLOAD = W * H // 8
HERE = os.path.dirname(os.path.abspath(__file__))
ASSETS = os.path.join(HERE, "assets")
BIN_PATH = os.path.join(ASSETS, "omarchy-oled-128x40.bin")
FRAMES_PATH = os.path.join(ASSETS, "omarchy-oled-128x40.frames")
GIF_PATH = os.path.join(ASSETS, "omarchy-oled-128x40.gif")
DELAY_MS = 100
MAGIC = b"OLEDGIF1"

# Ink bounding boxes for O M A R C H Y on the current 128x40 wordmark.
LETTERS = [
    (3, 16),
    (20, 41),
    (44, 58),
    (60, 74),
    (78, 93),
    (95, 109),
    (111, 124),
]


def unpack(data: bytes) -> list[list[int]]:
    pix = [[0] * W for _ in range(H)]
    for y in range(H):
        for x in range(W):
            i = y * W + x
            pix[y][x] = (data[i // 8] >> (7 - (i % 8))) & 1
    return pix


def pack(pix: list[list[int]]) -> bytes:
    out = bytearray(PAYLOAD)
    for y in range(H):
        for x in range(W):
            if pix[y][x]:
                i = y * W + x
                out[i // 8] |= 1 << (7 - (i % 8))
    return bytes(out)


def blank() -> list[list[int]]:
    return [[0] * W for _ in range(H)]


def copy_cols(src: list[list[int]], x0: int, x1: int, dst: list[list[int]]) -> None:
    for y in range(H):
        for x in range(x0, x1 + 1):
            dst[y][x] = src[y][x]


def draw_cursor(pix: list[list[int]], x: int) -> None:
    x0 = max(0, min(W - 3, x))
    for y in range(10, 29):
        for dx in range(3):
            pix[y][x0 + dx] = 1


def clone(pix: list[list[int]]) -> list[list[int]]:
    return [row[:] for row in pix]


def typewriter(src: list[list[int]]) -> list[bytes]:
    frames: list[bytes] = []
    shown = blank()
    frames.extend([pack(shown)] * 3)
    for i, (x0, x1) in enumerate(LETTERS):
        copy_cols(src, x0, x1, shown)
        with_cursor = clone(shown)
        if i + 1 < len(LETTERS):
            draw_cursor(with_cursor, LETTERS[i + 1][0])
        else:
            draw_cursor(with_cursor, LETTERS[-1][1] + 2)
        frames.append(pack(with_cursor))
        frames.append(pack(clone(shown)))
    # Two cursor blinks on the finished wordmark.
    cursor = clone(src)
    draw_cursor(cursor, LETTERS[-1][1] + 2)
    frames.extend([pack(cursor), pack(clone(src)), pack(cursor), pack(clone(src))])
    return frames


def hold(src: list[list[int]], n: int) -> list[bytes]:
    return [pack(src)] * n


def scanline(src: list[list[int]]) -> list[bytes]:
    frames: list[bytes] = []
    for y0 in range(0, H, 4):
        pix = clone(src)
        for y in range(y0, min(H, y0 + 3)):
            for x in range(W):
                pix[y][x] ^= 1
        frames.append(pack(pix))
    return frames


def wipe_out(src: list[list[int]], steps: int = 12) -> list[bytes]:
    frames: list[bytes] = []
    for i in range(1, steps + 1):
        cut = int(W * i / steps)
        pix = clone(src)
        for y in range(H):
            for x in range(cut):
                pix[y][x] = 0
        frames.append(pack(pix))
    frames.extend([pack(blank())] * 3)
    return frames


def build_frames(src: list[list[int]]) -> list[bytes]:
    frames: list[bytes] = []
    frames.extend(typewriter(src))
    frames.extend(hold(src, 18))
    frames.extend(scanline(src))
    frames.extend(hold(src, 12))
    frames.extend(wipe_out(src))
    return frames


def write_frames_blob(frames: list[bytes], path: str) -> None:
    blob = MAGIC + struct.pack("<HH", len(frames), DELAY_MS) + b"".join(frames)
    with open(path, "wb") as fh:
        fh.write(blob)


def write_gif(frames: list[bytes], path: str) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        pngs = []
        for i, frame in enumerate(frames):
            raw = os.path.join(tmp, f"f{i:03d}.gray")
            png = os.path.join(tmp, f"f{i:03d}.png")
            with open(raw, "wb") as fh:
                fh.write(frame)
            subprocess.check_call(
                [
                    "magick",
                    "-size",
                    f"{W}x{H}",
                    "-depth",
                    "1",
                    f"gray:{raw}",
                    png,
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            pngs.append(png)
        subprocess.check_call(
            [
                "magick",
                "-delay",
                str(DELAY_MS // 10),
                "-loop",
                "0",
                *pngs,
                "-colors",
                "2",
                path,
            ]
        )


def main() -> None:
    src = unpack(open(BIN_PATH, "rb").read())
    frames = build_frames(src)
    write_frames_blob(frames, FRAMES_PATH)
    write_gif(frames, GIF_PATH)
    print(f"wrote {len(frames)} frames ({DELAY_MS} ms) to {FRAMES_PATH}")
    print(f"wrote {GIF_PATH}")


if __name__ == "__main__":
    main()
