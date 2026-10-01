#!/usr/bin/env python3
"""Write the Omarchy wordmark (static or looping GIF) to a SteelSeries Apex OLED.

Stdlib only. Talks to the vendor HID interface over hidraw.
"""
from __future__ import annotations

import glob
import json
import os
import shutil
import stat
import struct
import subprocess
import sys
import tempfile
import time
import urllib.request
from urllib.parse import urlparse

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
STILL_RESEND_S = 0.5
# --live: how often to check the drawing file the panel's Create tab writes.
LIVE_TICK_S = 0.04
LIVE_MAX_BYTES = 4096
DEFAULT_DELAY_S = 0.10
OLED_W, OLED_H = 128, 40
MAX_IMPORT_BYTES = 40 * 1024 * 1024
MAX_IMPORT_FRAMES = 96
# Only host the "grab a random gif" panel button is allowed to reach.
IMPORT_URL_HOSTS = {"www.nlog.us", "nlog.us"}
# The real wordmark is a one-off logotype font with glyphs only for O/M/A/R/C/H/Y.
# "omarchy" extends that same 15-unit-grid construction to the full alphabet,
# digits, and basic punctuation, so short lockups match the logo exactly. It
# has no lowercase (maps to caps) or extended punctuation. Any other --font
# value (e.g. the old "jetbrains") falls back to it.
TEXT_FONTS = {
    "omarchy": os.path.join(HERE, "assets", "fonts", "OmarchyBlock-Regular.ttf"),
}
DEFAULT_TEXT_FONT = "omarchy"
MAX_TEXT_CHARS = 24
TEXT_STYLES = {"typewriter", "static", "spin", "waves"}


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


def ensure_private_dir(path: str) -> None:
    """Create/verify a plugin-owned 0700 state directory, refusing (not
    repairing) anything already there that is not a real directory we own —
    a symlink planted at this predictable path must not be followed."""
    os.makedirs(path, mode=0o700, exist_ok=True)
    st = os.lstat(path)
    if not stat.S_ISDIR(st.st_mode):
        raise SystemExit(f"refusing to use non-directory at {path}")
    if st.st_uid != os.geteuid():
        raise SystemExit(f"refusing to use directory not owned by this user: {path}")
    if st.st_mode & 0o077:
        os.chmod(path, 0o700)


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


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    # The host/scheme allowlist below only re-runs on the URL we built; a
    # redirect response would hand urlopen's default handler a new URL that
    # never sees that check, which is exactly how an allowlisted host could
    # point the fetch at an internal address. Refuse every redirect instead.
    def redirect_request(self, *args, **kwargs):
        return None


_NO_REDIRECT_OPENER = urllib.request.build_opener(_NoRedirect)


def fetch_url_to_tmp(url: str) -> str:
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.hostname not in IMPORT_URL_HOSTS:
        raise SystemExit("url not allowed")
    ext = os.path.splitext(parsed.path)[1].lower()
    if ext not in {".gif", ".png", ".jpg", ".jpeg", ".webp", ".bmp"}:
        ext = ".gif"
    req = urllib.request.Request(url, headers={"User-Agent": "omarchy-steelseries-oled"})
    with _NO_REDIRECT_OPENER.open(req, timeout=15) as resp:
        data = resp.read(MAX_IMPORT_BYTES + 1)
    if len(data) > MAX_IMPORT_BYTES:
        raise SystemExit("file too large")
    tmp_dir = tempfile.mkdtemp(prefix="steelseries-oled-")
    name = os.path.basename(parsed.path) or ("download" + ext)
    tmp_path = os.path.join(tmp_dir, name)
    with open(tmp_path, "wb") as fh:
        fh.write(data)
    return tmp_path


def render_text_bitmap(text: str, font: str) -> bytes:
    magick = shutil.which("magick")
    if magick is None:
        raise SystemExit("ImageMagick (magick) is required to render text")
    text = text.strip()
    if not text:
        raise SystemExit("empty text")
    if len(text) > MAX_TEXT_CHARS:
        raise SystemExit(f"text too long (max {MAX_TEXT_CHARS} characters)")
    font_path = TEXT_FONTS.get(font, TEXT_FONTS[DEFAULT_TEXT_FONT])
    if not os.path.isfile(font_path):
        raise SystemExit("text font not found")
    with tempfile.TemporaryDirectory() as tmp:
        # ImageMagick's label:/caption: coders treat a value starting with "@"
        # as "read the label from this file" rather than as literal text, so
        # typed text is written to a file we control and referenced by path
        # instead of being interpolated into the label: argument directly —
        # otherwise typing e.g. "@/etc/hostname" would rasterize that file's
        # contents instead of the literal string.
        text_path = os.path.join(tmp, "label.txt")
        with open(text_path, "w", encoding="utf-8") as fh:
            fh.write(text)
        label_arg = f"label:@{text_path}"
        probe = os.path.join(tmp, "probe.png")
        best = 8
        for size in range(40, 5, -1):
            subprocess.check_call(
                [magick, "-background", "black", "-fill", "white",
                 "-font", font_path, "-pointsize", str(size), label_arg, probe],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
            w = int(subprocess.check_output([magick, "identify", "-format", "%w", probe]))
            h = int(subprocess.check_output([magick, "identify", "-format", "%h", probe]))
            if w <= OLED_W - 4 and h <= OLED_H - 2:
                best = size
                break
        raw_path = os.path.join(tmp, "text.raw")
        subprocess.check_call(
            [
                magick,
                "-background", "black",
                "-fill", "white",
                "-font", font_path,
                "-pointsize", str(best),
                label_arg,
                "-gravity", "center",
                "-background", "black",
                "-extent", f"{OLED_W}x{OLED_H}",
                "-colorspace", "Gray",
                "-threshold", "50%",
                "-depth", "1",
                f"gray:{raw_path}",
            ],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        blob = open(raw_path, "rb").read()
    if len(blob) != PAYLOAD_BYTES:
        raise SystemExit("could not rasterize text")
    return blob


def animate_bitmap(blob: bytes, style: str) -> tuple[list[bytes], int]:
    """The wordmark's typewriter/static/spin/waves treatments, applied to any
    128x40 bitmap. make_gif splits the ink into column runs and animates each
    run like a letter, so separate shapes in a drawing move independently."""
    import make_gif

    pix = make_gif.unpack(blob)
    letters = make_gif.segment_letters(pix)
    if style == "spin":
        return make_gif.build_spin(pix, letters), 70
    if style == "waves":
        return make_gif.build_waves(pix, letters), 60
    if style == "static":
        return [make_gif.pack(pix)], 100
    cursor_rows = make_gif.ink_row_bounds(pix)
    return make_gif.build_frames(pix, letters, cursor_rows), 100


def render_drawing(
    hex_text: str,
    style: str,
    out_dir: str,
    preview_suffix: str | None = None,
) -> dict[str, object]:
    """Animate a Create-tab drawing (1280 hex chars) into draw.frames."""
    if style not in TEXT_STYLES:
        style = "typewriter"
    try:
        blob = bytes.fromhex(hex_text.strip())
    except ValueError:
        raise SystemExit("bad drawing")
    if len(blob) != PAYLOAD_BYTES:
        raise SystemExit("bad drawing")
    frames, delay_ms = animate_bitmap(blob, style)
    ensure_private_dir(out_dir)
    preview_png, preview_gif = preview_paths(out_dir, "draw-preview", preview_suffix)
    with open(os.path.join(out_dir, "draw.frames"), "wb") as fh:
        fh.write(FRAMES_MAGIC + struct.pack("<HH", len(frames), delay_ms) + b"".join(frames))
    with open(os.path.join(out_dir, "draw.bin"), "wb") as fh:
        fh.write(blob)
    magick = shutil.which("magick")
    if magick is None:
        raise SystemExit("ImageMagick (magick) is required to preview drawings")
    write_preview(frames, delay_ms, preview_png, preview_gif, magick)
    result = {"type": "drawing", "style": style, "frames": len(frames), "delay_ms": delay_ms}
    print(json.dumps(result, separators=(",", ":")), flush=True)
    return result


def render_text(
    text: str,
    style: str,
    out_dir: str,
    font: str = DEFAULT_TEXT_FONT,
    preview_suffix: str | None = None,
) -> dict[str, object]:
    if style not in TEXT_STYLES:
        style = "typewriter"
    if font not in TEXT_FONTS:
        font = DEFAULT_TEXT_FONT
    blob = render_text_bitmap(text, font)
    frames, delay_ms = animate_bitmap(blob, style)

    ensure_private_dir(out_dir)
    frames_path = os.path.join(out_dir, "text.frames")
    rest_path = os.path.join(out_dir, "text.bin")
    preview_png, preview_gif = preview_paths(out_dir, "text-preview", preview_suffix)
    with open(frames_path, "wb") as fh:
        fh.write(FRAMES_MAGIC + struct.pack("<HH", len(frames), delay_ms) + b"".join(frames))
    with open(rest_path, "wb") as fh:
        fh.write(blob)
    magick = shutil.which("magick")
    write_preview(frames, delay_ms, preview_png, preview_gif, magick)
    result = {
        "type": "text",
        "label": text,
        "style": style,
        "font": font,
        "frames": len(frames),
        "delay_ms": delay_ms,
    }
    print(json.dumps(result, separators=(",", ":")), flush=True)
    return result


def import_image(
    src: str,
    out_dir: str,
    threshold: int = 50,
    label_override: str | None = None,
    delay_scale: float | None = None,
    preview_suffix: str | None = None,
) -> dict[str, object]:
    magick = shutil.which("magick")
    if magick is None:
        raise SystemExit("ImageMagick (magick) is required to import images")
    src = os.path.realpath(src)
    if not os.path.isfile(src):
        raise SystemExit("not a file")
    if os.path.getsize(src) > MAX_IMPORT_BYTES:
        raise SystemExit("file too large")
    threshold = clamp_int(threshold, 5, 95, 50)
    ensure_private_dir(out_dir)
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
    if delay_scale:
        delay_ms = clamp_int(round(delay_ms * delay_scale), 50, 500, delay_ms)
    frames_path = os.path.join(out_dir, "custom.frames")
    rest_path = os.path.join(out_dir, "custom.bin")
    preview_png, preview_gif = preview_paths(out_dir, "preview", preview_suffix)
    with open(frames_path, "wb") as fh:
        fh.write(FRAMES_MAGIC + struct.pack("<HH", len(frames), delay_ms) + b"".join(frames))
    with open(rest_path, "wb") as fh:
        fh.write(frames[0])
    write_preview(frames, delay_ms, preview_png, preview_gif, magick)
    label = label_override or os.path.basename(src)
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


def preview_paths(out_dir: str, base: str, preview_suffix: str | None) -> tuple[str, str]:
    # Qt's AnimatedImage cache can serve a stale frame if the same preview
    # path is overwritten in place, even with cache-busting URL tricks — so
    # give each revision its own filename and drop the previous one.
    for old in glob.glob(os.path.join(out_dir, f"{base}*.png")) + glob.glob(os.path.join(out_dir, f"{base}*.gif")):
        try:
            os.remove(old)
        except OSError:
            pass
    name = f"{base}-{preview_suffix}" if preview_suffix else base
    return os.path.join(out_dir, f"{name}.png"), os.path.join(out_dir, f"{name}.gif")


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
        send_feature_fd(fd, bytes(PAYLOAD_BYTES))
        return path
    finally:
        os.close(fd)


def emit(state: str, **fields: object) -> None:
    payload: dict[str, object] = {"type": "status", "state": state}
    payload.update(fields)
    print(json.dumps(payload, separators=(",", ":")), flush=True)


def file_sig(path: str) -> tuple[int, int, int] | None:
    try:
        st = os.stat(path)
    except OSError:
        return None
    return (st.st_ino, st.st_mtime_ns, st.st_size)


def load_live(path: str, invert: bool) -> bytes | None:
    """One frame as 1280 hex chars (row-major, MSB-first, like the .bin
    files). Anything else is ignored so a half-written file is harmless."""
    try:
        with open(path, "rb") as fh:
            raw = fh.read(LIVE_MAX_BYTES + 1)
    except OSError:
        return None
    if len(raw) > LIVE_MAX_BYTES:
        return None
    try:
        payload = bytes.fromhex(raw.decode("ascii").strip())
    except (UnicodeDecodeError, ValueError):
        return None
    if len(payload) != PAYLOAD_BYTES:
        return None
    return invert_payload(payload) if invert else payload


def frame_hex(path: str) -> str:
    """First frame of a .bin or .frames file as hex, for seeding a drawing."""
    blob = open(path, "rb").read(PAYLOAD_BYTES + 12 + 1)
    if blob[:8] == FRAMES_MAGIC:
        blob = blob[12 : 12 + PAYLOAD_BYTES]
    if len(blob) < PAYLOAD_BYTES:
        raise SystemExit(f"no {PAYLOAD_BYTES}-byte frame in {path}")
    return blob[:PAYLOAD_BYTES].hex()


def watch(
    frames: list[bytes],
    delay_s: float,
    live_path: str | None = None,
    invert: bool = False,
) -> None:
    # The shell doesn't always take its children with it (omarchy-restart-shell
    # leaves them reparented to the user manager), and an orphan keeps
    # streaming its old preset interleaved with the new shell's. Bail out as
    # soon as the parent that launched us is gone.
    parent = os.getppid()
    # A still only needs re-sending often enough to win the panel back from
    # the firmware's own overlays (volume, media keys), not at full frame rate.
    # In --live mode we tick fast to notice edits, but still only resend an
    # unchanged frame at that slower rate.
    still = len(set(frames)) == 1
    tick = LIVE_TICK_S if live_path else (max(delay_s, STILL_RESEND_S) if still else delay_s)
    live_sig: tuple[int, int, int] | None = None
    dirty = True
    last_send = 0.0
    last = None
    denied: set[str] = set()
    fd: int | None = None
    path: str | None = None
    idx = 0
    next_frame = time.monotonic()
    while True:
        if os.getppid() != parent:
            return
        # Scanning sysfs costs ~2 ms, so only do it while there's no open
        # device; an unplug or replug surfaces as a failed write instead.
        if fd is None:
            path = find_oled_hidraw()
            if path != last:
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
        if live_path is not None:
            sig = file_sig(live_path)
            if sig != live_sig:
                live_sig = sig
                payload = load_live(live_path, invert)
                if payload is not None and payload != frames[0]:
                    frames = [payload]
                    dirty = True
        if fd is not None and (
            dirty or not still or time.monotonic() - last_send >= STILL_RESEND_S - 0.005
        ):
            try:
                send_feature_fd(fd, frames[idx % len(frames)])
                idx += 1
                dirty = False
                last_send = time.monotonic()
            except OSError as exc:
                # A vanished node is just an unplug; the rescan reports it.
                if path is not None and os.path.exists(path):
                    emit("error", path=path, message=str(exc))
                try:
                    os.close(fd)
                except OSError:
                    pass
                fd = None
                last = None
                dirty = True
        if fd is None:
            time.sleep(POLL_SECONDS)
            next_frame = time.monotonic()
            dirty = True
            continue
        # Hold a steady frame clock rather than sleeping a full delay after
        # each variable-length USB write, and never burst to catch up.
        next_frame += tick
        wait = next_frame - time.monotonic()
        if wait < 0:
            next_frame = time.monotonic()
            wait = 0
        time.sleep(wait)


def main() -> None:
    raw = sys.argv[1:]
    invert = "--invert" in raw
    watch_mode = "--watch" in raw
    once = "--once" in raw or "--static" in raw
    release_mode = "--release" in raw
    args = [a for a in raw if a not in {"--invert", "--watch", "--once", "--static", "--release"}]
    import_src = take_opt(args, "--import")
    import_url = take_opt(args, "--import-url")
    out_dir = take_opt(args, "--out-dir")
    # --frames may repeat; the loops play back to back (delay from the first).
    frames_paths = []
    while "--frames" in args:
        frames_paths.append(take_opt(args, "--frames"))
    rest_path = take_opt(args, "--rest")
    threshold_opt = take_opt(args, "--threshold")
    delay_opt = take_opt(args, "--delay-ms")
    label_opt = take_opt(args, "--label")
    delay_scale_opt = take_opt(args, "--delay-scale")
    preview_suffix_opt = take_opt(args, "--preview-suffix")
    render_text_opt = take_opt(args, "--render-text")
    style_opt = take_opt(args, "--style")
    font_opt = take_opt(args, "--font")
    live_opt = take_opt(args, "--live")
    hex_opt = take_opt(args, "--hex")
    render_drawing_opt = take_opt(args, "--render-drawing")
    if render_drawing_opt is not None:
        default_dir = os.path.join(os.path.expanduser("~"), ".local", "state", "omarchy", "steelseries-oled")
        render_drawing(render_drawing_opt, style_opt or "typewriter", out_dir or default_dir, preview_suffix_opt)
        return
    if hex_opt is not None:
        print(frame_hex(hex_opt), flush=True)
        return
    if release_mode:
        try:
            path = apply_once(bytes(PAYLOAD_BYTES), args[0] if args else None)
        except FileNotFoundError:
            print("Cleared OLED (no keyboard)")
            return
        print(f"Cleared OLED on {path}")
        return
    if render_text_opt is not None:
        default_dir = os.path.join(os.path.expanduser("~"), ".local", "state", "omarchy", "steelseries-oled")
        render_text(
            render_text_opt,
            style_opt or "typewriter",
            out_dir or default_dir,
            font=font_opt or DEFAULT_TEXT_FONT,
            preview_suffix=preview_suffix_opt,
        )
        return
    if import_src or import_url:
        default_dir = os.path.join(os.path.expanduser("~"), ".local", "state", "omarchy", "steelseries-oled")
        delay_scale = float(delay_scale_opt) if delay_scale_opt else None
        if import_url:
            tmp_path = fetch_url_to_tmp(import_url)
            try:
                import_image(tmp_path, out_dir or default_dir, threshold=clamp_int(threshold_opt, 5, 95, 50), label_override=label_opt, delay_scale=delay_scale, preview_suffix=preview_suffix_opt)
            finally:
                shutil.rmtree(os.path.dirname(tmp_path), ignore_errors=True)
        else:
            import_image(import_src, out_dir or default_dir, threshold=clamp_int(threshold_opt, 5, 95, 50), label_override=label_opt, delay_scale=delay_scale, preview_suffix=preview_suffix_opt)
        return
    delay_override = clamp_int(delay_opt, 50, 500, 0) if delay_opt is not None else None
    if delay_override == 0:
        delay_override = None
    if live_opt is not None:
        if not watch_mode:
            raise SystemExit("--live needs --watch")
        watch([load_live(live_opt, invert) or bytes(PAYLOAD_BYTES)], DEFAULT_DELAY_S, live_opt, invert)
        return
    rest = load_static(invert, rest_path)
    frames, delay_s = load_frames(invert, frames_paths[0] if frames_paths else None, delay_override)
    for extra in frames_paths[1:]:
        frames.extend(load_frames(invert, extra, delay_override)[0])
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
