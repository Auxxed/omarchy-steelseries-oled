#!/usr/bin/env python3
"""Write the Omarchy wordmark to a SteelSeries Apex OLED (128x40, 1-bit).

Stdlib only. Talks to the vendor HID interface over hidraw.
"""
from __future__ import annotations

import glob
import os
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
POLL_SECONDS = 2.0


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


def send_feature(dev_path: str, payload: bytes) -> None:
    import fcntl

    report = bytes([REPORT_ID]) + payload + bytes([0x00])
    variants = [report, bytes([0x00]) + report]
    last_err: OSError | None = None
    fd = os.open(dev_path, os.O_RDWR)
    try:
        for buf in variants:
            try:
                fcntl.ioctl(fd, hid_ioc_sfeature(len(buf)), bytearray(buf))
                return
            except OSError as exc:
                last_err = exc
        raise OSError(str(last_err))
    finally:
        os.close(fd)


def load_payload(invert: bool) -> bytes:
    payload = bytearray(open(BIN_PATH, "rb").read())
    if len(payload) != PAYLOAD_BYTES:
        raise SystemExit(f"expected {PAYLOAD_BYTES}-byte bitmap at {BIN_PATH}, got {len(payload)}")
    if invert:
        payload = bytearray(b ^ 0xFF for b in payload)
    return bytes(payload)


def apply_once(payload: bytes, dev: str | None = None) -> str:
    path = dev or find_oled_hidraw()
    if path is None:
        raise FileNotFoundError("no SteelSeries Apex OLED interface found")
    send_feature(path, payload)
    return path


def watch(payload: bytes) -> None:
    last = None
    denied: set[str] = set()
    while True:
        path = find_oled_hidraw()
        if path != last:
            last = path
            if path is None:
                print("steelseries-oled: keyboard disconnected", flush=True)
            else:
                try:
                    apply_once(payload, path)
                    denied.discard(path)
                    print(f"steelseries-oled: wrote Omarchy image to {path}", flush=True)
                except PermissionError:
                    if path not in denied:
                        print(
                            f"steelseries-oled: cannot write {path} (need udev rule; see README)",
                            file=sys.stderr,
                            flush=True,
                        )
                        denied.add(path)
                except OSError as exc:
                    print(f"steelseries-oled: {exc}", file=sys.stderr, flush=True)
        time.sleep(POLL_SECONDS)


def main() -> None:
    args = [a for a in sys.argv[1:] if a != "--invert"]
    invert = "--invert" in sys.argv
    watch_mode = "--watch" in args
    args = [a for a in args if a != "--watch"]
    payload = load_payload(invert)
    if watch_mode:
        watch(payload)
        return
    dev = args[0] if args else None
    path = apply_once(payload, dev)
    print(f"Wrote Omarchy OLED image to {path}")


if __name__ == "__main__":
    main()
