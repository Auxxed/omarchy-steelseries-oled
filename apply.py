#!/usr/bin/env python3
"""Write the Omarchy wordmark (static or looping GIF) to a SteelSeries Apex OLED.

Stdlib only. Talks to the vendor HID interface over hidraw.
"""
from __future__ import annotations

import glob
import json
import os
import struct
import sys
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
FRAMES_MAGIC = b"OLEDGIF1"
POLL_SECONDS = 2.0
DEFAULT_DELAY_S = 0.10


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


def load_static(invert: bool) -> bytes:
    payload = bytearray(open(BIN_PATH, "rb").read())
    if len(payload) != PAYLOAD_BYTES:
        raise SystemExit(f"expected {PAYLOAD_BYTES}-byte bitmap at {BIN_PATH}, got {len(payload)}")
    if invert:
        payload = bytearray(b ^ 0xFF for b in payload)
    return bytes(payload)


def load_frames(invert: bool) -> tuple[list[bytes], float]:
    if not os.path.isfile(FRAMES_PATH):
        return [load_static(invert)], DEFAULT_DELAY_S
    blob = open(FRAMES_PATH, "rb").read()
    header_len = 8 + 4
    if len(blob) < header_len or blob[:8] != FRAMES_MAGIC:
        raise SystemExit(f"bad frames file at {FRAMES_PATH}")
    count, delay_ms = struct.unpack_from("<HH", blob, 8)
    expected = header_len + count * PAYLOAD_BYTES
    if count < 1 or len(blob) != expected:
        raise SystemExit(f"expected {expected} bytes in {FRAMES_PATH}, got {len(blob)}")
    frames = []
    off = header_len
    for _ in range(count):
        frame = blob[off : off + PAYLOAD_BYTES]
        frames.append(invert_payload(frame) if invert else frame)
        off += PAYLOAD_BYTES
    delay_s = (delay_ms / 1000.0) if delay_ms else DEFAULT_DELAY_S
    return frames, delay_s


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
        send_feature_fd(fd, rest)
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
    args = [a for a in raw if a not in {"--invert", "--watch", "--once", "--static"}]
    rest = load_static(invert)
    frames, delay_s = load_frames(invert)
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
