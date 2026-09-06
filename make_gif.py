#!/usr/bin/env python3
"""Build the looping 128x40 1-bit Omarchy OLED GIF from the static wordmark.

Stdlib for the frame data. ImageMagick (`magick`) writes the .gif preview.
"""
from __future__ import annotations

import math
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
STATIC_FRAMES = os.path.join(ASSETS, "omarchy-oled-static.frames")
SPIN_FRAMES = os.path.join(ASSETS, "omarchy-oled-spin.frames")
SPIN_GIF = os.path.join(ASSETS, "omarchy-oled-spin.gif")
WAVES_FRAMES = os.path.join(ASSETS, "omarchy-oled-waves.frames")
WAVES_GIF = os.path.join(ASSETS, "omarchy-oled-waves.gif")
DELAY_MS = 100
SPIN_DELAY_MS = 70
WAVES_DELAY_MS = 60
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


def draw_cursor(pix: list[list[int]], x: int, y0: int = 10, y1: int = 28) -> None:
    x0 = max(0, min(W - 3, x))
    for y in range(y0, y1 + 1):
        for dx in range(3):
            pix[y][x0 + dx] = 1


def segment_letters(pix: list[list[int]]) -> list[tuple[int, int]]:
    """Group ink into column runs so typewriter/spin/waves can animate
    arbitrary rendered text the same way they animate the fixed wordmark."""
    col_ink = [any(pix[y][x] for y in range(H)) for x in range(W)]
    boxes: list[tuple[int, int]] = []
    x = 0
    while x < W:
        if col_ink[x]:
            x0 = x
            while x < W and col_ink[x]:
                x += 1
            boxes.append((x0, x - 1))
        else:
            x += 1
    return boxes or [(0, W - 1)]


def ink_row_bounds(pix: list[list[int]]) -> tuple[int, int]:
    rows = [y for y in range(H) if any(pix[y])]
    if not rows:
        return (10, 28)
    return (min(rows), max(rows))


def clone(pix: list[list[int]]) -> list[list[int]]:
    return [row[:] for row in pix]


def typewriter(
    src: list[list[int]],
    letters: list[tuple[int, int]] = LETTERS,
    cursor_rows: tuple[int, int] = (10, 28),
) -> list[bytes]:
    frames: list[bytes] = []
    shown = blank()
    frames.extend([pack(shown)] * 3)
    for i, (x0, x1) in enumerate(letters):
        copy_cols(src, x0, x1, shown)
        with_cursor = clone(shown)
        if i + 1 < len(letters):
            draw_cursor(with_cursor, letters[i + 1][0], *cursor_rows)
        else:
            draw_cursor(with_cursor, letters[-1][1] + 2, *cursor_rows)
        frames.append(pack(with_cursor))
        frames.append(pack(clone(shown)))
    # Two cursor blinks on the finished wordmark.
    cursor = clone(src)
    draw_cursor(cursor, letters[-1][1] + 2, *cursor_rows)
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


def plot(pix: list[list[int]], x: int, y: int) -> None:
    if 0 <= x < W and 0 <= y < H:
        pix[y][x] = 1


def blit_letter_spin(
    src: list[list[int]],
    x0: int,
    x1: int,
    dst: list[list[int]],
    theta: float,
    dy: int = 0,
) -> None:
    width = x1 - x0 + 1
    cx = (x0 + x1) / 2.0
    scale = abs(math.cos(theta))
    back = math.cos(theta) < 0
    sink = int(round((1.0 - scale) * 1.5))
    if scale < 0.14:
        spine = int(round(cx))
        for y in range(H):
            sy = y - dy + sink
            if sy < 0 or sy >= H:
                continue
            if any(src[sy][x] for x in range(x0, x1 + 1)):
                plot(dst, spine, y)
                plot(dst, spine + (1 if back else -1), y)
        return
    new_w = max(1, int(round(width * scale)))
    for y in range(H):
        sy = y - dy + sink
        if sy < 0 or sy >= H:
            continue
        for i in range(new_w):
            t = i / max(1, new_w - 1)
            src_x = int(round(x1 - t * (width - 1) if back else x0 + t * (width - 1)))
            dst_x = int(round(cx - (new_w - 1) / 2.0 + i))
            if src[sy][src_x]:
                plot(dst, dst_x, y)


def compose_spin(
    src: list[list[int]], thetas: list[float], letters: list[tuple[int, int]] = LETTERS
) -> list[list[int]]:
    pix = blank()
    for (x0, x1), theta in zip(letters, thetas):
        blit_letter_spin(src, x0, x1, pix, theta)
    return pix


def build_spin(src: list[list[int]], letters: list[tuple[int, int]] = LETTERS) -> list[bytes]:
    frames: list[bytes] = []
    n_letters = len(letters)
    steps = 12
    # Cascade: each letter takes a full turn, slightly staggered.
    stagger = 2
    cascade = stagger * (n_letters - 1) + steps
    for n in range(cascade):
        thetas = []
        for i in range(n_letters):
            local = n - i * stagger
            if local < 0:
                thetas.append(0.0)
            elif local < steps:
                thetas.append(2 * math.pi * local / steps)
            else:
                thetas.append(0.0)
        frames.append(pack(compose_spin(src, thetas, letters)))
    frames.extend(hold(src, 4))
    # Whole word tumble, two turns.
    together = 16
    for n in range(together * 2):
        theta = 2 * math.pi * n / together
        thetas = [theta + i * 0.18 for i in range(n_letters)]
        frames.append(pack(compose_spin(src, thetas, letters)))
    frames.extend(hold(src, 6))
    return frames


def blit_letter_shift(
    src: list[list[int]], x0: int, x1: int, dst: list[list[int]], dy: int
) -> None:
    for y in range(H):
        sy = y - dy
        if sy < 0 or sy >= H:
            continue
        for x in range(x0, x1 + 1):
            if src[sy][x]:
                dst[y][x] = 1


def compose_wave(
    src: list[list[int]], dys: list[int], letters: list[tuple[int, int]] = LETTERS
) -> list[list[int]]:
    pix = blank()
    for (x0, x1), dy in zip(letters, dys):
        blit_letter_shift(src, x0, x1, pix, dy)
    return pix


def build_waves(
    src: list[list[int]], letters: list[tuple[int, int]] = LETTERS, cycles: int = 2
) -> list[bytes]:
    frames: list[bytes] = []
    n_letters = len(letters)
    steps = 24
    amplitude = 4
    phase_step = (2 * math.pi / n_letters) * 1.1
    for n in range(steps * cycles):
        t = 2 * math.pi * n / steps
        dys = [int(round(amplitude * math.sin(t - i * phase_step))) for i in range(n_letters)]
        frames.append(pack(compose_wave(src, dys, letters)))
    frames.extend(hold(src, 6))
    return frames


def build_frames(
    src: list[list[int]],
    letters: list[tuple[int, int]] = LETTERS,
    cursor_rows: tuple[int, int] = (10, 28),
) -> list[bytes]:
    frames: list[bytes] = []
    frames.extend(typewriter(src, letters, cursor_rows))
    frames.extend(hold(src, 18))
    frames.extend(scanline(src))
    frames.extend(hold(src, 12))
    frames.extend(wipe_out(src))
    return frames


def write_frames_blob(frames: list[bytes], path: str, delay_ms: int = DELAY_MS) -> None:
    blob = MAGIC + struct.pack("<HH", len(frames), delay_ms) + b"".join(frames)
    with open(path, "wb") as fh:
        fh.write(blob)


def write_gif(frames: list[bytes], path: str, delay_ms: int = DELAY_MS) -> None:
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
                str(max(1, delay_ms // 10)),
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
    write_frames_blob([pack(src)], STATIC_FRAMES)
    print(f"wrote 1 static frame to {STATIC_FRAMES}")
    spin = build_spin(src)
    write_frames_blob(spin, SPIN_FRAMES, SPIN_DELAY_MS)
    write_gif(spin, SPIN_GIF, SPIN_DELAY_MS)
    print(f"wrote {len(spin)} frames ({SPIN_DELAY_MS} ms) to {SPIN_FRAMES}")
    print(f"wrote {SPIN_GIF}")
    waves = build_waves(src)
    write_frames_blob(waves, WAVES_FRAMES, WAVES_DELAY_MS)
    write_gif(waves, WAVES_GIF, WAVES_DELAY_MS)
    print(f"wrote {len(waves)} frames ({WAVES_DELAY_MS} ms) to {WAVES_FRAMES}")
    print(f"wrote {WAVES_GIF}")


if __name__ == "__main__":
    main()
