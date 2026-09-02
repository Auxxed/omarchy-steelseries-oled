#!/usr/bin/env python3
"""Write the Omarchy wordmark (static or looping GIF) to a SteelSeries Apex OLED.

Stdlib only. Talks to the vendor HID interface over hidraw.
"""
from __future__ import annotations

import glob
import json
import os
import shutil
import struct
import subprocess
import sys
import tempfile
import time

VID = 0x1038
# Apex Pro, Apex 7, Apex Pro TKL, Apex 7 TKL, Apex 5 — same 128x40 legacy OLED.
PIDS = {0x1610, 0x1612, 0x1614, 0x1618, 0x161C}
OLED_INTERFACE = 1
REPORT_ID = 0x61
PAYLOAD_BYTES = 128 * 40 // 8  # 640
HERE = os.path.dirname(os.path.abspath(__file__))
BIN_PATH = os.path.join(HERE, "assets", "omarchy-oled-128x40.bin")
FRAMES_PATH = os.path.join(HERE, "assets", "omarchy-oled-128x40.frames")
IDLE_PATH = os.path.join(HERE, "assets", "steelseries-idle.bin")
FRAMES_MAGIC = b"OLEDGIF1"
POLL_SECONDS = 2.0
DEFAULT_DELAY_S = 0.10
OLED_W, OLED_H = 128, 40
MAX_IMPORT_BYTES = 40 * 1024 * 1024
MAX_IMPORT_FRAMES = 96


def hid_ioc_sfeature(length: int) -> int:
    ioc_read, ioc_write = 2, 1
    return ((ioc_read | ioc_write) << 30) | (length << 16) | (ord("H") << 8) | 0x06


def usb_parent_attr(sysfs_hidraw: str, name: str) -> str | None:
    path = os.path.realpath(sysfs_hidraw)
    for _ in range(8):
        path = os.path.dirname(path)
        candidate = os.path.join(path, name)
        if os.path.isfile(candidate):
            with open(candidate, encoding="utf-8") as fh:
                return fh.read().strip()
    return None


def find_oled_hidraw() -> str | None:
    for sysfs in glob.glob("/sys/class/hidraw/hidraw*/device"):
        hidraw = "/dev/" + os.path.basename(os.path.dirname(sysfs))
        vid = usb_parent_attr(sysfs, "idVendor")
        pid = usb_parent_attr(sysfs, "idProduct")
        iface = usb_parent_attr(sysfs, "bInterfaceNumber")
        if vid is None or pid is None or iface is None:
            continue
        try:
            if int(vid, 16) == VID and int(pid, 16) in PIDS and int(iface, 16) == OLED_INTERFACE:
                return hidraw
        except ValueError:
            continue
    return None


def send_feature_fd(fd: int, payload: bytes) -> None:
    report = bytes([REPORT_ID]) + payload + bytes([0x00])
    variants = [report, bytes([0x00]) + report]
    last_err: OSError | None = None
    import fcntl

    for buf in variants:
        try:
            fcntl.ioctl(fd, hid_ioc_sfeature(len(buf)), bytearray(buf))
            return
        except OSError as exc:
            last_err = exc
    raise OSError(str(last_err))


def send_feature(dev_path: str, payload: bytes) -> None:
    fd = os.open(dev_path, os.O_RDWR)
    try:
        send_feature_fd(fd, payload)
    finally:
        os.close(fd)


def invert_payload(payload: bytes) -> bytes:
    return bytes(b ^ 0xFF for b in payload)


def load_static(invert: bool, path: str | None = None) -> bytes:
    src = path or BIN_PATH
    payload = bytearray(open(src, "rb").read())
    if len(payload) != PAYLOAD_BYTES:
        raise SystemExit(f"expected {PAYLOAD_BYTES}-byte bitmap at {src}, got {len(payload)}")
    if invert:
        payload = bytearray(b ^ 0xFF for b in payload)
    return bytes(payload)


def clamp_int(value: object, lo: int, hi: int, default: int) -> int:
    try:
        number = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default
    return max(lo, min(hi, number))


def load_frames(
    invert: bool,
    path: str | None = None,
    delay_override_ms: int | None = None,
) -> tuple[list[bytes], float]:
    src = path or FRAMES_PATH
    if not os.path.isfile(src):
        delay_s = DEFAULT_DELAY_S
        if delay_override_ms is not None:
            delay_s = clamp_int(delay_override_ms, 50, 500, 100) / 1000.0
        return [load_static(invert)], delay_s
    blob = open(src, "rb").read()
    header_len = 8 + 4
    if len(blob) < header_len or blob[:8] != FRAMES_MAGIC:
        raise SystemExit(f"bad frames file at {src}")
    count, delay_ms = struct.unpack_from("<HH", blob, 8)
    expected = header_len + count * PAYLOAD_BYTES
    if count < 1 or len(blob) != expected:
        raise SystemExit(f"expected {expected} bytes in {src}, got {len(blob)}")
    frames = []
    off = header_len
    for _ in range(count):
        frame = blob[off : off + PAYLOAD_BYTES]
        frames.append(invert_payload(frame) if invert else frame)
        off += PAYLOAD_BYTES
    if delay_override_ms is not None:
        delay_ms = clamp_int(delay_override_ms, 50, 500, delay_ms or 100)
    delay_s = (delay_ms / 1000.0) if delay_ms else DEFAULT_DELAY_S
    return frames, delay_s


def take_opt(args: list[str], name: str) -> str | None:
    if name not in args:
        return None
    i = args.index(name)
    if i + 1 >= len(args):
        raise SystemExit(f"{name} needs a path")
    value = args[i + 1]
    del args[i : i + 2]
    return value


def sample_frames(frames: list[bytes], max_n: int) -> list[bytes]:
    if len(frames) <= max_n:
        return frames
    if max_n <= 1:
        return [frames[0]]
    out = []
    last = len(frames) - 1
    for i in range(max_n):
        out.append(frames[round(i * last / (max_n - 1))])
    return out


def gif_delay_ms(src: str) -> int:
    try:
        out = subprocess.check_output(
            ["magick", "identify", "-format", "%T\n", src],
            stderr=subprocess.DEVNULL,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return 0
    ticks = []
    for line in out.splitlines():
        line = line.strip()
        if line.isdigit():
            ticks.append(int(line))
    ticks = [t for t in ticks if t > 0]
    if not ticks:
        return 0
    return max(50, min(500, ticks[0] * 10))


def import_image(src: str, out_dir: str, threshold: int = 50) -> dict[str, object]:
    magick = shutil.which("magick")
    if magick is None:
        raise SystemExit("ImageMagick (magick) is required to import images")
    src = os.path.realpath(src)
    if not os.path.isfile(src):
        raise SystemExit("not a file")
    if os.path.getsize(src) > MAX_IMPORT_BYTES:
        raise SystemExit("file too large")
    threshold = clamp_int(threshold, 5, 95, 50)
    os.makedirs(out_dir, exist_ok=True)
    ext = os.path.splitext(src)[1].lower()
    if ext not in {".gif", ".png", ".jpg", ".jpeg", ".webp", ".bmp"}:
        ext = ".img"
    source_name = "source" + ext
    dest_source = os.path.join(out_dir, source_name)
    for old in glob.glob(os.path.join(out_dir, "source.*")):
        if os.path.abspath(old) == os.path.abspath(src):
            continue
        try:
            os.remove(old)
        except OSError:
            pass
    if os.path.abspath(src) != os.path.abspath(dest_source):
        shutil.copy2(src, dest_source)
    with tempfile.TemporaryDirectory() as tmp:
        raw_path = os.path.join(tmp, "all.raw")
        try:
            subprocess.check_call(
                [
                    magick,
                    src,
                    "-coalesce",
                    "-auto-orient",
                    "-background",
                    "black",
                    "-gravity",
                    "center",
                    "-resize",
                    f"{OLED_W}x{OLED_H}",
                    "-extent",
                    f"{OLED_W}x{OLED_H}",
                    "-colorspace",
                    "Gray",
                    "-threshold",
                    f"{threshold}%",
                    "-depth",
                    "1",
                    f"gray:{raw_path}",
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except (OSError, subprocess.CalledProcessError):
            raise SystemExit("could not convert image")
        blob = open(raw_path, "rb").read()
    if len(blob) < PAYLOAD_BYTES or len(blob) % PAYLOAD_BYTES != 0:
        raise SystemExit("could not convert image")
    frames = [blob[i : i + PAYLOAD_BYTES] for i in range(0, len(blob), PAYLOAD_BYTES)]
    frames = sample_frames(frames, MAX_IMPORT_FRAMES)
    delay_ms = gif_delay_ms(src) or int(DEFAULT_DELAY_S * 1000)
    frames_path = os.path.join(out_dir, "custom.frames")
    rest_path = os.path.join(out_dir, "custom.bin")
    preview_png = os.path.join(out_dir, "preview.png")
    preview_gif = os.path.join(out_dir, "preview.gif")
    with open(frames_path, "wb") as fh:
        fh.write(FRAMES_MAGIC + struct.pack("<HH", len(frames), delay_ms) + b"".join(frames))
    with open(rest_path, "wb") as fh:
        fh.write(frames[0])
    write_preview(frames, delay_ms, preview_png, preview_gif, magick)
    label = os.path.basename(src)
    print(
        json.dumps(
            {
                "type": "imported",
                "label": label,
                "source": source_name,
                "frames": len(frames),
                "delay_ms": delay_ms,
                "threshold": threshold,
            },
            separators=(",", ":"),
        ),
        flush=True,
    )
    return {"label": label, "frames": len(frames)}


def write_preview(
    frames: list[bytes],
    delay_ms: int,
    png_path: str,
    gif_path: str,
    magick: str,
) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        pngs: list[str] = []
        for i, payload in enumerate(frames):
            raw = os.path.join(tmp, f"f{i:03d}.gray")
            png = os.path.join(tmp, f"f{i:03d}.png")
            with open(raw, "wb") as fh:
                fh.write(payload)
            subprocess.check_call(
                [
                    magick,
                    "-size",
                    f"{OLED_W}x{OLED_H}",
                    "-depth",
                    "1",
                    f"gray:{raw}",
                    png,
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            pngs.append(png)
        shutil.copy2(pngs[0], png_path)
        subprocess.check_call(
            [
                magick,
                "-delay",
                str(max(1, delay_ms // 10)),
                "-loop",
                "0",
                *pngs,
                "-colors",
                "2",
                gif_path,
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )


def apply_once(payload: bytes, dev: str | None = None) -> str:
    path = dev or find_oled_hidraw()
    if path is None:
        raise FileNotFoundError("no SteelSeries Apex OLED interface found")
    send_feature(path, payload)
    return path


def open_oled(dev: str | None = None) -> tuple[str, int]:
    path = dev or find_oled_hidraw()
    if path is None:
        raise FileNotFoundError("no SteelSeries Apex OLED interface found")
    return path, os.open(path, os.O_RDWR)


def play_loop(frames: list[bytes], delay_s: float, rest: bytes, dev: str | None = None) -> str:
    path, fd = open_oled(dev)
    idx = 0
    try:
        while True:
            send_feature_fd(fd, frames[idx % len(frames)])
            idx += 1
            time.sleep(delay_s)
    except KeyboardInterrupt:
        idle = load_static(False, IDLE_PATH) if os.path.isfile(IDLE_PATH) else rest
        send_feature_fd(fd, idle)
        return path
    finally:
        os.close(fd)


def emit(state: str, **fields: object) -> None:
    payload: dict[str, object] = {"type": "status", "state": state}
    payload.update(fields)
    print(json.dumps(payload, separators=(",", ":")), flush=True)


def watch(frames: list[bytes], delay_s: float) -> None:
    last = None
    denied: set[str] = set()
    fd: int | None = None
    idx = 0
    while True:
        path = find_oled_hidraw()
        if path != last:
            if fd is not None:
                try:
                    os.close(fd)
                except OSError:
                    pass
                fd = None
            last = path
            idx = 0
            if path is None:
                emit("disconnected")
            else:
                try:
                    fd = os.open(path, os.O_RDWR)
                    denied.discard(path)
                    emit("looping", path=path)
                except PermissionError:
                    emit("denied", path=path)
                    if path not in denied:
                        print(
                            f"steelseries-oled: cannot write {path} "
                            "(install the udev rule, then unplug/replug)",
                            file=sys.stderr,
                            flush=True,
                        )
                        denied.add(path)
                    last = None
                except OSError as exc:
                    emit("error", path=path, message=str(exc))
                    last = None
        if fd is not None:
            try:
                send_feature_fd(fd, frames[idx % len(frames)])
                idx += 1
            except OSError as exc:
                emit("error", path=path, message=str(exc))
                try:
                    os.close(fd)
                except OSError:
                    pass
                fd = None
                last = None
        time.sleep(delay_s if fd is not None else POLL_SECONDS)


def main() -> None:
    raw = sys.argv[1:]
    invert = "--invert" in raw
    watch_mode = "--watch" in raw
    once = "--once" in raw or "--static" in raw
    release_mode = "--release" in raw
    args = [a for a in raw if a not in {"--invert", "--watch", "--once", "--static", "--release"}]
    import_src = take_opt(args, "--import")
    out_dir = take_opt(args, "--out-dir")
    frames_path = take_opt(args, "--frames")
    rest_path = take_opt(args, "--rest")
    threshold_opt = take_opt(args, "--threshold")
    delay_opt = take_opt(args, "--delay-ms")
    if release_mode:
        payload = open(IDLE_PATH, "rb").read() if os.path.isfile(IDLE_PATH) else bytes(PAYLOAD_BYTES)
        if len(payload) != PAYLOAD_BYTES:
            raise SystemExit(f"expected {PAYLOAD_BYTES}-byte idle bitmap at {IDLE_PATH}, got {len(payload)}")
        try:
            path = apply_once(payload, args[0] if args else None)
        except FileNotFoundError:
            print("Released OLED (no keyboard)")
            return
        print(f"Released OLED to SteelSeries idle on {path}")
        return
    if import_src:
        import_image(
            import_src,
            out_dir or os.path.join(os.path.expanduser("~"), ".local", "state", "omarchy", "steelseries-oled"),
            threshold=clamp_int(threshold_opt, 5, 95, 50),
        )
        return
    delay_override = clamp_int(delay_opt, 50, 500, 0) if delay_opt is not None else None
    if delay_override == 0:
        delay_override = None
    rest = load_static(invert, rest_path)
    frames, delay_s = load_frames(invert, frames_path, delay_override)
    if watch_mode:
        watch(frames, delay_s)
        return
    dev = args[0] if args else None
    if once or len(frames) == 1:
        path = apply_once(rest, dev)
        print(f"Wrote Omarchy OLED image to {path}")
        return
    print(f"Looping {len(frames)}-frame Omarchy GIF — Ctrl-C to stop and leave the wordmark", flush=True)
    path = play_loop(frames, delay_s, rest, dev)
    print(f"Stopped on {path}")


if __name__ == "__main__":
    main()
