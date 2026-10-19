#!/usr/bin/python3 -I
"""Write the Omarchy wordmark (static or looping GIF) to a SteelSeries Apex OLED.

Stdlib only, plus ImageMagick (/usr/bin/magick) for imports, text and the
info screens. Talks to the vendor HID interface over hidraw.

Streaming (the shell keeps one of these running):
  apply.py --watch [--invert] --frames F [--frames F ...] --rest R [--delay-ms N]
  apply.py --watch --live - [--live-hex HEX]   frames as hex lines on stdin
  apply.py --watch --info clock|system|media   media details as JSON on stdin

One-shot jobs for the panel, each printing one JSON line:
  --import PATH | --import-url URL, --render-text TEXT, --render-drawing HEX
  --hex PATH          first frame of a .bin or .frames file, as hex
  --release           blank the OLED
  --have              which of custom/text/drawing exist
  --read-settings | --read-drawing      print the saved file (nothing if none)
  --write-settings | --write-drawing    save what arrives on stdin

Every state file is read through one descriptor opened without following
links and checked to be a small regular file of ours, and written as a
fresh 0600 temporary renamed into place, so a link planted at a state path
is replaced rather than written through.
"""
from __future__ import annotations

import errno
import glob
import json
import os
import re
import select
import stat
import struct
import subprocess
import sys
import tempfile
import time
import urllib.request
from urllib.parse import urlparse

HERE = os.path.dirname(os.path.abspath(__file__))
# make_gif sits beside this file; -I keeps the script's folder off sys.path.
sys.path.insert(0, HERE)

VID = 0x1038
# Apex Pro, Apex 7, Apex Pro TKL, Apex 7 TKL, Apex 5 — same 128x40 legacy OLED.
PIDS = {0x1610, 0x1612, 0x1614, 0x1618, 0x161C}
OLED_INTERFACE = 1
REPORT_ID = 0x61
PAYLOAD_BYTES = 128 * 40 // 8  # 640
BIN_PATH = os.path.join(HERE, "assets", "omarchy-oled-128x40.bin")
FRAMES_PATH = os.path.join(HERE, "assets", "omarchy-oled-128x40.frames")
FRAMES_MAGIC = b"OLEDGIF1"
FRAMES_HEADER = 12
MAX_FRAMES_BYTES = 16 * 1024 * 1024
POLL_SECONDS = 2.0
STILL_RESEND_S = 0.5
# --live: how often to look at stdin for a new frame from the Create tab.
LIVE_TICK_S = 0.04
MAX_STDIN_LINE = 8192
DEFAULT_DELAY_S = 0.10
OLED_W, OLED_H = 128, 40
MAX_IMPORT_BYTES = 40 * 1024 * 1024
MAX_IMPORT_FRAMES = 96
MAX_SETTINGS_BYTES = 512 * 1024
DRAWING_HEX_CHARS = PAYLOAD_BYTES * 2
MAGICK = "/usr/bin/magick"
SYSFS_HIDRAW = "/sys/class/hidraw"
DEVICE_RE = re.compile(r"/dev/hidraw\d{1,3}")
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
INFO_MODES = ("clock", "system", "media")


# --- files ---------------------------------------------------------------------


def read_bounded(path: str, limit: int, owned: bool = False, virtual: bool = False) -> bytes:
    """The bytes of a regular file at path, read through one descriptor that
    was opened without following a final link and never blocks on a FIFO.
    Raises OSError for a missing, refused or oversized file. /proc and /sys
    files report sizes that mean nothing (0, 4096), so for those (virtual)
    only the read itself is capped."""
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode):
            raise OSError(errno.EINVAL, "not a regular file", path)
        if owned and st.st_uid != os.geteuid():
            raise OSError(errno.EPERM, "not ours", path)
        if not virtual and st.st_size > limit:
            raise OSError(errno.EFBIG, "too large", path)
        os.set_blocking(fd, True)
        data = b""
        while len(data) <= limit:
            chunk = os.read(fd, min(1 << 20, limit + 1 - len(data)))
            if not chunk:
                break
            data += chunk
        if len(data) > limit:
            raise OSError(errno.EFBIG, "grew past the limit", path)
        return data
    finally:
        os.close(fd)


def write_atomic(path: str, data: bytes) -> None:
    """Replace path with data: a fresh 0600 temporary in the same folder,
    synced, then renamed over it. A link at path is replaced, never followed."""
    folder = os.path.dirname(path)
    fd, tmp = tempfile.mkstemp(dir=folder, prefix="." + os.path.basename(path) + ".", suffix=".tmp")
    try:
        os.fchmod(fd, 0o600)
        view = memoryview(data)
        while view:
            view = view[os.write(fd, view):]
        os.fsync(fd)
        os.close(fd)
        fd = -1
        os.replace(tmp, path)
        tmp = ""
        dfd = os.open(folder, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
        try:
            os.fsync(dfd)
        finally:
            os.close(dfd)
    finally:
        if fd >= 0:
            os.close(fd)
        if tmp:
            try:
                os.unlink(tmp)
            except OSError:
                pass


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


def state_home() -> str:
    return os.environ.get("XDG_STATE_HOME") or os.path.join(os.path.expanduser("~"), ".local", "state")


def settings_path() -> str:
    return os.path.join(state_home(), "omarchy", "steelseries-oled.json")


def state_dir() -> str:
    return os.path.join(state_home(), "omarchy", "steelseries-oled")


def frames_blob(frames: list[bytes], delay_ms: int) -> bytes:
    return FRAMES_MAGIC + struct.pack("<HH", len(frames), delay_ms) + b"".join(frames)


def have_files(out_dir: str) -> dict[str, bool]:
    def regular(name: str) -> bool:
        try:
            st = os.lstat(os.path.join(out_dir, name))
        except OSError:
            return False
        return stat.S_ISREG(st.st_mode) and st.st_uid == os.geteuid()

    return {"custom": regular("custom.frames"), "text": regular("text.frames"), "drawing": regular("drawing.hex")}


def read_state(path: str, limit: int) -> int:
    """Print a saved state file. Nothing printed (exit 0) when there is none;
    exit 4 when the path holds something that is refused."""
    try:
        data = read_bounded(path, limit, owned=True)
    except FileNotFoundError:
        return 0
    except OSError as exc:
        print(f"refusing {path}: {exc.strerror or exc}", file=sys.stderr)
        return 4
    sys.stdout.write(data.decode("utf-8", "replace"))
    sys.stdout.flush()
    return 0


def stdin_bounded(limit: int) -> bytes:
    data = sys.stdin.buffer.read(limit + 1)
    if len(data) > limit:
        raise SystemExit("input too large")
    return data


def write_settings() -> None:
    data = stdin_bounded(MAX_SETTINGS_BYTES)
    try:
        parsed = json.loads(data.decode("utf-8"))
    except (ValueError, RecursionError):
        raise SystemExit("settings are not JSON")
    if not isinstance(parsed, dict):
        raise SystemExit("settings are not a JSON object")
    path = settings_path()
    os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
    write_atomic(path, data)


def write_drawing(out_dir: str) -> None:
    text = stdin_bounded(DRAWING_HEX_CHARS + 16).decode("ascii", "replace").strip()
    if parse_hex_frame(text) is None:
        raise SystemExit("bad drawing")
    ensure_private_dir(out_dir)
    write_atomic(os.path.join(out_dir, "drawing.hex"), (text + "\n").encode("ascii"))


# --- device ----------------------------------------------------------------------


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
                return fh.read(64).strip()
    return None


def find_oled_hidraw() -> str | None:
    for sysfs in sorted(glob.glob(os.path.join(SYSFS_HIDRAW, "hidraw*", "device"))):
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


def open_device(path: str) -> int:
    return os.open(path, os.O_RDWR | os.O_CLOEXEC)


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
    fd = open_device(dev_path)
    try:
        send_feature_fd(fd, payload)
    finally:
        os.close(fd)


# --- frames ------------------------------------------------------------------------


def invert_payload(payload: bytes) -> bytes:
    return bytes(b ^ 0xFF for b in payload)


def load_static(invert: bool, path: str | None = None) -> bytes:
    src = path or BIN_PATH
    try:
        payload = read_bounded(src, PAYLOAD_BYTES)
    except OSError as exc:
        raise SystemExit(f"cannot read {src}: {exc.strerror or exc}")
    if len(payload) != PAYLOAD_BYTES:
        raise SystemExit(f"expected {PAYLOAD_BYTES}-byte bitmap at {src}, got {len(payload)}")
    return invert_payload(payload) if invert else payload


def clamp_int(value: object, lo: int, hi: int, default: int) -> int:
    try:
        number = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default
    return max(lo, min(hi, number))


def parse_frames(blob: bytes, src: str = "frames") -> tuple[list[bytes], int]:
    if len(blob) < FRAMES_HEADER or blob[:8] != FRAMES_MAGIC:
        raise SystemExit(f"bad frames file at {src}")
    count, delay_ms = struct.unpack_from("<HH", blob, 8)
    expected = FRAMES_HEADER + count * PAYLOAD_BYTES
    if count < 1 or len(blob) != expected:
        raise SystemExit(f"expected {expected} bytes in {src}, got {len(blob)}")
    frames = [blob[FRAMES_HEADER + i * PAYLOAD_BYTES:FRAMES_HEADER + (i + 1) * PAYLOAD_BYTES] for i in range(count)]
    return frames, delay_ms


def load_frames(
    invert: bool,
    path: str | None = None,
    delay_override_ms: int | None = None,
) -> tuple[list[bytes], float]:
    src = path or FRAMES_PATH
    try:
        blob = read_bounded(src, MAX_FRAMES_BYTES)
    except FileNotFoundError:
        delay_s = DEFAULT_DELAY_S
        if delay_override_ms is not None:
            delay_s = clamp_int(delay_override_ms, 50, 500, 100) / 1000.0
        return [load_static(invert)], delay_s
    except OSError as exc:
        raise SystemExit(f"cannot read {src}: {exc.strerror or exc}")
    frames, delay_ms = parse_frames(blob, src)
    if invert:
        frames = [invert_payload(f) for f in frames]
    if delay_override_ms is not None:
        delay_ms = clamp_int(delay_override_ms, 50, 500, delay_ms or 100)
    delay_s = (delay_ms / 1000.0) if delay_ms else DEFAULT_DELAY_S
    return frames, delay_s


def take_opt(args: list[str], name: str) -> str | None:
    if name not in args:
        return None
    i = args.index(name)
    if i + 1 >= len(args):
        raise SystemExit(f"{name} needs a value")
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


def magick_or_exit(why: str) -> str:
    if not os.access(MAGICK, os.X_OK):
        raise SystemExit(f"ImageMagick ({MAGICK}) is required to {why}")
    return MAGICK


def gif_delay_ms(src: str) -> int:
    if not os.access(MAGICK, os.X_OK):
        return 0
    try:
        out = subprocess.run(
            [MAGICK, "identify", "-format", "%T\n", src],
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            timeout=30, check=True,
        ).stdout.decode("ascii", "replace")
    except (OSError, subprocess.SubprocessError):
        return 0
    ticks = [int(line) for line in out.split()[:4096] if line.isdigit() and int(line) > 0]
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
    if parsed.scheme != "https" or parsed.hostname not in IMPORT_URL_HOSTS or parsed.username or parsed.password:
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
    tmp_path = os.path.join(tmp_dir, "download" + ext)
    write_atomic(tmp_path, data)
    return tmp_path


def label_args(text_path: str, font_path: str, size: int) -> list[str]:
    # ImageMagick's label: coder treats a value starting with "@" as "read
    # the label from this file" rather than as literal text, so typed text is
    # always written to a file we control and referenced by path; typing
    # e.g. "@/etc/hostname" can then never rasterize that file instead.
    return ["-background", "black", "-fill", "white", "-font", font_path, "-pointsize", str(size), f"label:@{text_path}"]


def render_text_bitmap(text: str, font: str) -> bytes:
    magick = magick_or_exit("render text")
    text = text.strip()
    if not text:
        raise SystemExit("empty text")
    if len(text) > MAX_TEXT_CHARS:
        raise SystemExit(f"text too long (max {MAX_TEXT_CHARS} characters)")
    font_path = TEXT_FONTS.get(font, TEXT_FONTS[DEFAULT_TEXT_FONT])
    if not os.path.isfile(font_path):
        raise SystemExit("text font not found")
    with tempfile.TemporaryDirectory() as tmp:
        text_path = os.path.join(tmp, "label.txt")
        with open(text_path, "x", encoding="utf-8") as fh:
            fh.write(text)
        best = 8
        for size in range(40, 5, -1):
            dims = subprocess.run(
                [magick, *label_args(text_path, font_path, size), "-format", "%w %h", "info:"],
                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=30, check=True,
            ).stdout.split()
            w, h = int(dims[0]), int(dims[1])
            if w <= OLED_W - 4 and h <= OLED_H - 2:
                best = size
                break
        raw = subprocess.run(
            [magick, *label_args(text_path, font_path, best), "-gravity", "center", "-background", "black",
             "-extent", f"{OLED_W}x{OLED_H}", "-colorspace", "Gray", "-threshold", "50%", "-depth", "1", "gray:-"],
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=30, check=True,
        ).stdout
    if len(raw) != PAYLOAD_BYTES:
        raise SystemExit("could not rasterize text")
    return raw


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


def save_animation(out_dir: str, stem: str, frames: list[bytes], delay_ms: int, rest: bytes) -> None:
    write_atomic(os.path.join(out_dir, stem + ".frames"), frames_blob(frames, delay_ms))
    write_atomic(os.path.join(out_dir, stem + ".bin"), rest)


def render_drawing(
    hex_text: str,
    style: str,
    out_dir: str,
    preview_suffix: str | None = None,
) -> dict[str, object]:
    """Animate a Create-tab drawing (1280 hex chars) into draw.frames."""
    if style not in TEXT_STYLES:
        style = "typewriter"
    blob = parse_hex_frame(hex_text)
    if blob is None:
        raise SystemExit("bad drawing")
    magick = magick_or_exit("preview drawings")
    frames, delay_ms = animate_bitmap(blob, style)
    ensure_private_dir(out_dir)
    save_animation(out_dir, "draw", frames, delay_ms, blob)
    write_preview(frames, delay_ms, out_dir, "draw-preview", preview_suffix, magick)
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
    save_animation(out_dir, "text", frames, delay_ms, blob)
    write_preview(frames, delay_ms, out_dir, "text-preview", preview_suffix, MAGICK)
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


IMAGE_EXTS = {".gif", ".png", ".jpg", ".jpeg", ".webp", ".bmp"}


def read_picked(src: str) -> bytes:
    """The file the user picked, read once (links followed: it is their
    pick) through one descriptor that never blocks on a FIFO."""
    try:
        fd = os.open(src, os.O_RDONLY | os.O_NONBLOCK | os.O_CLOEXEC)
    except OSError:
        raise SystemExit("not a file")
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode):
            raise SystemExit("not a file")
        if st.st_size > MAX_IMPORT_BYTES:
            raise SystemExit("file too large")
        os.set_blocking(fd, True)
        data = b""
        while len(data) <= MAX_IMPORT_BYTES:
            chunk = os.read(fd, 1 << 20)
            if not chunk:
                break
            data += chunk
    finally:
        os.close(fd)
    if len(data) > MAX_IMPORT_BYTES:
        raise SystemExit("file too large")
    return data


def import_image(
    src: str,
    out_dir: str,
    threshold: int = 50,
    label_override: str | None = None,
    delay_scale: float | None = None,
    preview_suffix: str | None = None,
) -> dict[str, object]:
    magick = magick_or_exit("import images")
    data = read_picked(src)
    threshold = clamp_int(threshold, 5, 95, 50)
    ensure_private_dir(out_dir)
    ext = os.path.splitext(src)[1].lower()
    if ext not in IMAGE_EXTS:
        ext = ".img"
    source_name = "source" + ext
    dest_source = os.path.join(out_dir, source_name)
    # Everything after this works on the private copy, never the picked path.
    write_atomic(dest_source, data)
    for old in glob.glob(os.path.join(out_dir, "source.*")):
        if old != dest_source:
            try:
                os.remove(old)
            except OSError:
                pass
    try:
        raw = subprocess.run(
            [magick, dest_source, "-coalesce", "-auto-orient", "-background", "black", "-gravity", "center",
             "-resize", f"{OLED_W}x{OLED_H}", "-extent", f"{OLED_W}x{OLED_H}", "-colorspace", "Gray",
             "-threshold", f"{threshold}%", "-depth", "1", "gray:-"],
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=120, check=True,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        raise SystemExit("could not convert image")
    if len(raw) < PAYLOAD_BYTES or len(raw) % PAYLOAD_BYTES != 0:
        raise SystemExit("could not convert image")
    frames = [raw[i : i + PAYLOAD_BYTES] for i in range(0, len(raw), PAYLOAD_BYTES)]
    frames = sample_frames(frames, MAX_IMPORT_FRAMES)
    delay_ms = gif_delay_ms(dest_source) or int(DEFAULT_DELAY_S * 1000)
    if delay_scale:
        delay_ms = clamp_int(round(delay_ms * delay_scale), 50, 500, delay_ms)
    save_animation(out_dir, "custom", frames, delay_ms, frames[0])
    write_preview(frames, delay_ms, out_dir, "preview", preview_suffix, magick)
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


def write_preview(
    frames: list[bytes],
    delay_ms: int,
    out_dir: str,
    base: str,
    preview_suffix: str | None,
    magick: str,
) -> None:
    """<base>[-suffix].png and .gif in out_dir. Qt's AnimatedImage cache can
    serve a stale frame if the same preview path is overwritten in place,
    even with cache-busting URL tricks, so each revision gets its own name
    and the previous ones are dropped. Both are built in a private temporary
    folder inside out_dir and renamed into place."""
    if not os.access(magick, os.X_OK):
        return
    for old in glob.glob(os.path.join(out_dir, f"{base}*.png")) + glob.glob(os.path.join(out_dir, f"{base}*.gif")):
        try:
            os.remove(old)
        except OSError:
            pass
    name = f"{base}-{preview_suffix}" if preview_suffix else base
    with tempfile.TemporaryDirectory(dir=out_dir, prefix=".preview-") as tmp:
        pngs: list[str] = []
        for i, payload in enumerate(frames):
            raw = os.path.join(tmp, f"f{i:03d}.gray")
            png = os.path.join(tmp, f"f{i:03d}.png")
            with open(raw, "xb") as fh:
                fh.write(payload)
            subprocess.run([magick, "-size", f"{OLED_W}x{OLED_H}", "-depth", "1", f"gray:{raw}", png],
                           stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                           timeout=30, check=True)
            pngs.append(png)
        gif = os.path.join(tmp, "out.gif")
        subprocess.run([magick, "-delay", str(max(1, delay_ms // 10)), "-loop", "0", *pngs, "-colors", "2", gif],
                       stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                       timeout=60, check=True)
        os.replace(pngs[0], os.path.join(out_dir, name + ".png"))
        os.replace(gif, os.path.join(out_dir, name + ".gif"))


def frame_hex(path: str) -> str:
    """First frame of a .bin or .frames file as hex, for seeding a drawing.
    Only the plugin's own assets and its state folder are read."""
    real = os.path.realpath(path)
    roots = (os.path.realpath(os.path.join(HERE, "assets")), os.path.realpath(state_dir()))
    if not any(real.startswith(root + os.sep) for root in roots):
        raise SystemExit("--hex only reads the plugin's assets and its own state")
    try:
        blob = read_bounded(path, MAX_FRAMES_BYTES)
    except OSError as exc:
        raise SystemExit(f"cannot read {path}: {exc.strerror or exc}")
    if blob[:8] == FRAMES_MAGIC:
        blob = blob[FRAMES_HEADER:FRAMES_HEADER + PAYLOAD_BYTES]
    if len(blob) < PAYLOAD_BYTES:
        raise SystemExit(f"no {PAYLOAD_BYTES}-byte frame in {path}")
    return blob[:PAYLOAD_BYTES].hex()


def parse_hex_frame(text: str) -> bytes | None:
    text = text.strip()
    if len(text) != DRAWING_HEX_CHARS:
        return None
    try:
        payload = bytes.fromhex(text)
    except ValueError:
        return None
    return payload if len(payload) == PAYLOAD_BYTES else None


# --- sources -------------------------------------------------------------------------


class Frames:
    """A loop of frames. A loop of one is a still, re-sent only often enough
    to win the panel back from the firmware's own overlays (volume, media
    keys), not at full frame rate."""

    def __init__(self, frames: list[bytes], delay_s: float):
        self.frames = frames
        self.still = len(set(frames)) == 1
        self.tick = max(delay_s, STILL_RESEND_S) if self.still else delay_s
        self.idx = 0

    def frame(self, now: float) -> bytes:
        out = self.frames[self.idx % len(self.frames)]
        self.idx += 1
        return out

    def feed(self, line: str) -> bool:
        return False


class Live(Frames):
    """The Create tab's canvas: each line on stdin is a whole frame as hex."""

    def __init__(self, initial: bytes, invert: bool):
        super().__init__([initial], DEFAULT_DELAY_S)
        self.invert = invert
        self.tick = LIVE_TICK_S

    def feed(self, line: str) -> bool:
        payload = parse_hex_frame(line)
        if payload is None:
            return False
        if self.invert:
            payload = invert_payload(payload)
        if payload == self.frames[0]:
            return False
        self.frames = [payload]
        return True


class Canvas:
    def __init__(self):
        self.pix = bytearray(OLED_W * OLED_H)

    def blit(self, glyph: tuple[int, int, bytes], x: int, y: int) -> None:
        w, h, bits = glyph
        for gy in range(h):
            yy = y + gy
            if not 0 <= yy < OLED_H:
                continue
            row = gy * w
            for gx in range(w):
                xx = x + gx
                if 0 <= xx < OLED_W and bits[row + gx]:
                    self.pix[yy * OLED_W + xx] = 1

    def rect(self, x: int, y: int, w: int, h: int, fill: bool = True) -> None:
        for yy in range(max(0, y), min(OLED_H, y + h)):
            for xx in range(max(0, x), min(OLED_W, x + w)):
                edge = yy in (y, y + h - 1) or xx in (x, x + w - 1)
                if fill or edge:
                    self.pix[yy * OLED_W + xx] = 1

    def pack(self) -> bytes:
        out = bytearray(PAYLOAD_BYTES)
        for i, v in enumerate(self.pix):
            if v:
                out[i >> 3] |= 0x80 >> (i & 7)
        return bytes(out)


class Labels:
    """Text rasterized by ImageMagick in the Omarchy Block font, trimmed to
    its ink, and cached: the clock changes once a minute and the stats draw
    from a small set of strings, so magick rarely runs twice for one."""

    def __init__(self, font: str = TEXT_FONTS[DEFAULT_TEXT_FONT], render=None):
        self.font = font
        self.cache: dict[tuple[str, int], tuple[int, int, bytes]] = {}
        self.render = render or self.magick_render

    def get(self, text: str, size: int) -> tuple[int, int, bytes]:
        key = (text, size)
        if key not in self.cache:
            if len(self.cache) > 256:
                self.cache.clear()
            try:
                self.cache[key] = self.render(text, size) if text.strip() else (0, 0, b"")
            except (OSError, ValueError, subprocess.SubprocessError):
                self.cache[key] = (0, 0, b"")
        return self.cache[key]

    def magick_render(self, text: str, size: int) -> tuple[int, int, bytes]:
        with tempfile.TemporaryDirectory() as tmp:
            text_path = os.path.join(tmp, "label.txt")
            with open(text_path, "x", encoding="utf-8") as fh:
                fh.write(text[:120])
            out = subprocess.run(
                [MAGICK, *label_args(text_path, self.font, size), "-flatten", "-colorspace", "Gray",
                 "-threshold", "50%", "-trim", "+repage", "-depth", "8", "-format", "%w %h\n", "-write", "info:-",
                 "gray:-"],
                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=20, check=True,
            ).stdout
        head, _, body = out.partition(b"\n")
        w, h = (int(v) for v in head.split())
        if w * h != len(body) or w > 4096 or h > 64:
            return 0, 0, b""
        return w, h, bytes(1 if b >= 128 else 0 for b in body)


PROC_STAT = "/proc/stat"
PROC_MEMINFO = "/proc/meminfo"
HWMON_ROOT = "/sys/class/hwmon"
CPU_SENSORS = ("k10temp", "coretemp", "zenpower", "cpu_thermal")


def cpu_times() -> tuple[int, int] | None:
    try:
        first = read_bounded(PROC_STAT, 1 << 20, virtual=True).split(b"\n", 1)[0].split()
    except OSError:
        return None
    if len(first) < 5 or first[0] != b"cpu":
        return None
    try:
        values = [int(v) for v in first[1:]]
    except ValueError:
        return None
    idle = values[3] + (values[4] if len(values) > 4 else 0)
    return sum(values), idle


def memory_used() -> float | None:
    try:
        text = read_bounded(PROC_MEMINFO, 1 << 20, virtual=True).decode("ascii", "replace")
    except OSError:
        return None
    fields = {}
    for line in text.splitlines():
        name, _, rest = line.partition(":")
        parts = rest.split()
        if parts and parts[0].isdigit():
            fields[name] = int(parts[0])
    total, avail = fields.get("MemTotal"), fields.get("MemAvailable")
    if not total or avail is None:
        return None
    return max(0.0, min(1.0, 1 - avail / total))


def cpu_temperature() -> float | None:
    """The CPU package temperature in C: the first hwmon chip named after a
    CPU sensor driver, its first input (Tctl / Package id 0)."""
    for chip in sorted(glob.glob(os.path.join(HWMON_ROOT, "hwmon*"))):
        try:
            name = read_bounded(os.path.join(chip, "name"), 64, virtual=True).decode("ascii", "replace").strip()
        except OSError:
            continue
        if name not in CPU_SENSORS:
            continue
        for sensor in sorted(glob.glob(os.path.join(chip, "temp*_input")))[:1]:
            try:
                return int(read_bounded(sensor, 64, virtual=True).strip()) / 1000.0
            except (OSError, ValueError):
                continue
    return None


def clean_label(value: object, limit: int) -> str:
    """What the Omarchy Block font can draw: printable ASCII, upper case."""
    text = "".join(ch if " " <= ch <= "~" else (" " if ch.isspace() else "") for ch in str(value or ""))
    return " ".join(text.upper().split())[:limit]


class Info:
    """Clock, system stats, or what is playing, drawn fresh each tick. The
    clock shifts a pixel or two each minute so no segment burns in; with
    nothing playing, the media screen shows the clock."""

    MONTHS = ("JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC")
    DAYS = ("MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN")

    def __init__(self, mode: str, labels: Labels | None = None, clock=time.localtime, invert: bool = False):
        self.mode = mode if mode in INFO_MODES else "clock"
        self.labels = labels or Labels()
        self.clock = clock
        self.invert = invert
        self.tick = STILL_RESEND_S if self.mode != "media" else 0.08
        self.still = self.mode != "media"
        self.media: dict[str, object] = {}
        self.cpu_prev = cpu_times()
        self.cpu_value = 0.0
        self.stats_at = 0.0
        self.scroll = 0
        # While the panel is open it asks for each frame back, to draw it.
        self.preview = False

    def feed(self, line: str) -> bool:
        try:
            msg = json.loads(line)
        except (ValueError, RecursionError):
            return False
        if not isinstance(msg, dict):
            return False
        if isinstance(msg.get("preview"), bool):
            self.preview = msg["preview"]
            return self.preview
        if not isinstance(msg.get("media"), dict):
            return False
        raw = msg["media"]
        progress = raw.get("progress")
        media = {
            "title": clean_label(raw.get("title"), 80),
            "artist": clean_label(raw.get("artist"), 60),
            "playing": raw.get("playing") is True,
            "progress": float(progress) if isinstance(progress, (int, float)) and 0 <= progress <= 1 else -1.0,
        }
        if media.get("title") != self.media.get("title"):
            self.scroll = 0
        self.media = media
        return True

    def frame(self, now: float) -> bytes:
        canvas = Canvas()
        if self.mode == "system":
            self.draw_system(canvas, now)
        elif self.mode == "media" and self.media.get("title"):
            self.draw_media(canvas)
        else:
            self.draw_clock(canvas)
        out = canvas.pack()
        # The panel draws the plain frame and inverts it with its own shader.
        self.plain = out
        return invert_payload(out) if self.invert else out

    def draw_clock(self, canvas: Canvas) -> None:
        t = self.clock()
        dx = t.tm_min % 5 - 2
        dy = t.tm_min % 3 - 1
        big = self.labels.get(f"{t.tm_hour:02d}:{t.tm_min:02d}", 30)
        date = self.labels.get(f"{self.DAYS[t.tm_wday]} {t.tm_mday} {self.MONTHS[t.tm_mon - 1]}", 10)
        gap = 4
        top = max(1, (OLED_H - big[1] - gap - date[1]) // 2)
        canvas.blit(big, (OLED_W - big[0]) // 2 + dx, top + dy)
        canvas.blit(date, (OLED_W - date[0]) // 2 + dx, top + big[1] + gap + dy)

    def read_stats(self, now: float) -> None:
        if now - self.stats_at < 2.0:
            return
        self.stats_at = now
        cur = cpu_times()
        if cur and self.cpu_prev and cur[0] > self.cpu_prev[0]:
            total = cur[0] - self.cpu_prev[0]
            idle = cur[1] - self.cpu_prev[1]
            self.cpu_value = max(0.0, min(1.0, 1 - idle / total))
        self.cpu_prev = cur

    def draw_system(self, canvas: Canvas, now: float) -> None:
        self.read_stats(now)
        mem = memory_used()
        temp = cpu_temperature()
        rows = [("CPU", self.cpu_value, str(round(self.cpu_value * 100))),
                ("RAM", mem or 0.0, str(round(mem * 100)) if mem is not None else "-"),
                ("TEMP", max(0.0, min(1.0, ((temp or 30.0) - 30) / 70)), str(round(temp)) if temp is not None else "-")]
        for i, (name, fraction, value) in enumerate(rows):
            y = 2 + i * 13
            canvas.blit(self.labels.get(name, 9), 1, y)
            bar_x, bar_w = 32, 68
            canvas.rect(bar_x, y + 1, bar_w, 7, fill=False)
            filled = round((bar_w - 4) * fraction)
            if filled > 0:
                canvas.rect(bar_x + 2, y + 3, filled, 3)
            number = self.labels.get(value, 9)
            canvas.blit(number, OLED_W - 1 - number[0], y)

    def draw_media(self, canvas: Canvas) -> None:
        title = self.labels.get(str(self.media.get("title") or ""), 10)
        artist = self.labels.get(str(self.media.get("artist") or ""), 9)
        top = 4
        if title[0] <= OLED_W:
            canvas.blit(title, (OLED_W - title[0]) // 2, top)
        else:
            # A title wider than the screen scrolls, with a gap between laps.
            lap = title[0] + 32
            x = -(self.scroll % lap)
            canvas.blit(title, x, top)
            canvas.blit(title, x + lap, top)
            self.scroll += 1
        if artist[0]:
            canvas.blit(artist, max(0, (OLED_W - artist[0]) // 2), top + max(title[1], 9) + 5)
        progress = self.media.get("progress")
        if isinstance(progress, float) and progress >= 0:
            canvas.rect(0, OLED_H - 2, OLED_W, 1)
            canvas.rect(0, OLED_H - 3, max(1, round(OLED_W * progress)), 3)
        if not self.media.get("playing"):
            canvas.rect(OLED_W - 8, OLED_H - 13, 2, 7)
            canvas.rect(OLED_W - 4, OLED_H - 13, 2, 7)


# --- streaming -------------------------------------------------------------------


def emit(state: str, **fields: object) -> None:
    payload: dict[str, object] = {"type": "status", "state": state}
    payload.update(fields)
    print(json.dumps(payload, separators=(",", ":")), flush=True)


class StdinLines:
    """Complete lines from a non-blocking stdin, each capped; EOF means the
    shell that started us is gone."""

    def __init__(self, fd: int = 0):
        self.fd = fd
        self.buf = b""
        self.closed = False
        os.set_blocking(fd, False)

    def wait(self, seconds: float) -> list[str]:
        if self.closed:
            time.sleep(max(0.0, seconds))
            return []
        ready, _, _ = select.select([self.fd], [], [], max(0.0, seconds))
        lines: list[str] = []
        if not ready:
            return lines
        try:
            chunk = os.read(self.fd, 65536)
        except BlockingIOError:
            return lines
        if not chunk:
            self.closed = True
            return lines
        self.buf += chunk
        while b"\n" in self.buf:
            line, self.buf = self.buf.split(b"\n", 1)
            if len(line) <= MAX_STDIN_LINE:
                lines.append(line.decode("utf-8", "replace"))
        if len(self.buf) > MAX_STDIN_LINE:
            self.buf = b""
        return lines


def watch(source, stdin: StdinLines | None = None) -> None:
    # The shell doesn't always take its children with it (omarchy-restart-shell
    # leaves them reparented to the user manager), and an orphan keeps
    # streaming its old preset interleaved with the new shell's. Bail out as
    # soon as the parent that launched us is gone, or stdin closes.
    parent = os.getppid()
    dirty = True
    last_send = 0.0
    last_preview = 0.0
    preview_frame = b""
    last = None
    denied: set[str] = set()
    fd: int | None = None
    path: str | None = None
    next_frame = time.monotonic()
    while True:
        if os.getppid() != parent or (stdin is not None and stdin.closed):
            return
        # Scanning sysfs costs ~2 ms, so only do it while there's no open
        # device; an unplug or replug surfaces as a failed write instead.
        if fd is None:
            path = find_oled_hidraw()
            if path != last:
                last = path
                if path is None:
                    emit("disconnected")
                else:
                    try:
                        fd = open_device(path)
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
        if fd is not None and (
            dirty or not source.still or time.monotonic() - last_send >= STILL_RESEND_S - 0.005
        ):
            try:
                frame = source.frame(time.monotonic())
                send_feature_fd(fd, frame)
                dirty = False
                last_send = time.monotonic()
                # At most four preview frames a second, and only new ones.
                if getattr(source, "preview", False) and frame != preview_frame and last_send - last_preview >= 0.25:
                    print(json.dumps({"type": "frame", "hex": getattr(source, "plain", frame).hex()}), flush=True)
                    preview_frame = frame
                    last_preview = last_send
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
            wait = POLL_SECONDS
            next_frame = time.monotonic() + POLL_SECONDS
            dirty = True
        else:
            # Hold a steady frame clock rather than sleeping a full delay after
            # each variable-length USB write, and never burst to catch up.
            next_frame += source.tick
            wait = next_frame - time.monotonic()
            if wait < 0:
                next_frame = time.monotonic()
                wait = 0
        if stdin is None:
            time.sleep(wait)
            continue
        deadline = time.monotonic() + wait
        while True:
            for line in stdin.wait(deadline - time.monotonic()):
                if source.feed(line):
                    dirty = True
            if stdin.closed or time.monotonic() >= deadline or dirty:
                break
        if dirty and fd is not None:
            next_frame = time.monotonic()


def device_arg(args: list[str]) -> str | None:
    """An explicit device for the manual modes: only a hidraw node."""
    if not args:
        return None
    if len(args) > 1 or not DEVICE_RE.fullmatch(args[0]):
        raise SystemExit(f"unexpected argument: {args[0]}")
    return args[0]


def apply_once(payload: bytes, dev: str | None = None) -> str:
    path = dev or find_oled_hidraw()
    if path is None:
        raise FileNotFoundError("no SteelSeries Apex OLED interface found")
    send_feature(path, payload)
    return path


def play_loop(frames: list[bytes], delay_s: float, dev: str | None = None) -> str:
    path = dev or find_oled_hidraw()
    if path is None:
        raise FileNotFoundError("no SteelSeries Apex OLED interface found")
    fd = open_device(path)
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


def main() -> None:
    # Everything this writes (state, previews, magick's output) is private.
    os.umask(0o077)
    raw = sys.argv[1:]
    flags = {"--invert", "--watch", "--once", "--static", "--release", "--have",
             "--read-settings", "--write-settings", "--read-drawing", "--write-drawing"}
    invert = "--invert" in raw
    watch_mode = "--watch" in raw
    once = "--once" in raw or "--static" in raw
    args = [a for a in raw if a not in flags]
    import_src = take_opt(args, "--import")
    import_url = take_opt(args, "--import-url")
    out_dir = take_opt(args, "--out-dir") or state_dir()
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
    live_hex_opt = take_opt(args, "--live-hex")
    info_opt = take_opt(args, "--info")
    hex_opt = take_opt(args, "--hex")
    render_drawing_opt = take_opt(args, "--render-drawing")
    if preview_suffix_opt is not None and not preview_suffix_opt.isdigit():
        raise SystemExit("--preview-suffix is a number")

    if "--have" in raw:
        print(json.dumps(have_files(out_dir), separators=(",", ":")), flush=True)
        return
    if "--read-settings" in raw:
        raise SystemExit(read_state(settings_path(), MAX_SETTINGS_BYTES))
    if "--write-settings" in raw:
        write_settings()
        return
    if "--read-drawing" in raw:
        raise SystemExit(read_state(os.path.join(out_dir, "drawing.hex"), DRAWING_HEX_CHARS + 16))
    if "--write-drawing" in raw:
        write_drawing(out_dir)
        return
    if render_drawing_opt is not None:
        render_drawing(render_drawing_opt, style_opt or "typewriter", out_dir, preview_suffix_opt)
        return
    if hex_opt is not None:
        print(frame_hex(hex_opt), flush=True)
        return
    if "--release" in raw:
        try:
            path = apply_once(bytes(PAYLOAD_BYTES), device_arg(args))
        except FileNotFoundError:
            print("Cleared OLED (no keyboard)")
            return
        print(f"Cleared OLED on {path}")
        return
    if render_text_opt is not None:
        render_text(render_text_opt, style_opt or "typewriter", out_dir,
                    font=font_opt or DEFAULT_TEXT_FONT, preview_suffix=preview_suffix_opt)
        return
    if import_src or import_url:
        try:
            delay_scale = float(delay_scale_opt) if delay_scale_opt else None
        except ValueError:
            raise SystemExit("--delay-scale is a number")
        if delay_scale is not None and not 0.1 <= delay_scale <= 10:
            raise SystemExit("--delay-scale is between 0.1 and 10")
        threshold = clamp_int(threshold_opt, 5, 95, 50)
        if import_url:
            tmp_path = fetch_url_to_tmp(import_url)
            try:
                import_image(tmp_path, out_dir, threshold=threshold, label_override=label_opt,
                             delay_scale=delay_scale, preview_suffix=preview_suffix_opt)
            finally:
                import shutil
                shutil.rmtree(os.path.dirname(tmp_path), ignore_errors=True)
        else:
            import_image(import_src, out_dir, threshold=threshold, label_override=label_opt,
                         delay_scale=delay_scale, preview_suffix=preview_suffix_opt)
        return
    delay_override = clamp_int(delay_opt, 50, 500, 0) if delay_opt is not None else None
    if delay_override == 0:
        delay_override = None
    if info_opt is not None:
        if not watch_mode:
            raise SystemExit("--info needs --watch")
        if info_opt not in INFO_MODES:
            raise SystemExit("--info is one of " + ", ".join(INFO_MODES))
        watch(Info(info_opt, invert=invert), StdinLines())
        return
    if live_opt is not None:
        if not watch_mode or live_opt != "-":
            raise SystemExit("--live - needs --watch (frames arrive on stdin)")
        initial = parse_hex_frame(live_hex_opt or "") or bytes(PAYLOAD_BYTES)
        watch(Live(invert_payload(initial) if invert else initial, invert), StdinLines())
        return
    dev = device_arg(args)
    rest = load_static(invert, rest_path)
    frames, delay_s = load_frames(invert, frames_paths[0] if frames_paths else None, delay_override)
    for extra in frames_paths[1:]:
        frames.extend(load_frames(invert, extra, delay_override)[0])
    if watch_mode:
        watch(Frames(frames, delay_s))
        return
    if once or len(frames) == 1:
        path = apply_once(rest, dev)
        print(f"Wrote Omarchy OLED image to {path}")
        return
    print(f"Looping {len(frames)}-frame Omarchy GIF — Ctrl-C to stop and leave the wordmark", flush=True)
    path = play_loop(frames, delay_s, dev)
    print(f"Stopped on {path}")


if __name__ == "__main__":
    main()
