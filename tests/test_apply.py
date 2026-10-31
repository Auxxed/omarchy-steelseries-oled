#!/usr/bin/python3 -I
"""Tests for apply.py: state files, frame files, the info screens, the Peak
takeover and the streaming loop. No keyboard is needed: the device is a
recorder, /proc and /sys are fake files, QuickPuff's daemon is a fake socket,
and the state lives in a throwaway XDG_STATE_HOME.

    python3 -I tests/test_apply.py
"""

import importlib.util
import json
import os
import pathlib
import socket
import struct
import subprocess
import sys
import tempfile
import threading
import time
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
STATE = tempfile.mkdtemp(prefix="oled-state-")
os.environ["XDG_STATE_HOME"] = STATE

spec = importlib.util.spec_from_file_location("apply", ROOT / "apply.py")
apply = importlib.util.module_from_spec(spec)
spec.loader.exec_module(apply)

HAVE_MAGICK = os.access(apply.MAGICK, os.X_OK)


def run(*args, stdin=b""):
    env = {"HOME": STATE, "XDG_STATE_HOME": STATE, "PATH": "/usr/bin", "LANG": "C.UTF-8"}
    return subprocess.run([sys.executable, "-I", str(ROOT / "apply.py"), *args], input=stdin,
                          capture_output=True, env=env, timeout=120)


def frames_file(path, frames, delay=100):
    with open(path, "wb") as f:
        f.write(apply.frames_blob(frames, delay))


class Files(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="oled-files-")

    def test_bounded_reads_refuse_links_fifos_and_oversize(self):
        real = os.path.join(self.dir, "real")
        with open(real, "wb") as f:
            f.write(b"x" * 10)
        link = os.path.join(self.dir, "link")
        os.symlink(real, link)
        fifo = os.path.join(self.dir, "fifo")
        os.mkfifo(fifo)
        self.assertEqual(apply.read_bounded(real, 10), b"x" * 10)
        for path in (link, fifo):
            with self.assertRaises(OSError):
                apply.read_bounded(path, 10)
        with self.assertRaises(OSError):
            apply.read_bounded(real, 9)

    def test_virtual_files_are_capped_by_the_read(self):
        fake = os.path.join(self.dir, "proc")
        with open(fake, "wb") as f:
            f.write(b"54000\n")
        self.assertEqual(apply.read_bounded(fake, 64, virtual=True), b"54000\n")

    def test_writes_replace_a_planted_link(self):
        victim = os.path.join(self.dir, "victim")
        with open(victim, "w") as f:
            f.write("must survive")
        target = os.path.join(self.dir, "state.json")
        os.symlink(victim, target)
        apply.write_atomic(target, b"{}")
        with open(victim) as f:
            self.assertEqual(f.read(), "must survive")
        self.assertFalse(os.path.islink(target))
        self.assertEqual(os.stat(target).st_mode & 0o777, 0o600)
        self.assertEqual(sorted(os.listdir(self.dir)), ["state.json", "victim"])

    def test_frames_files_are_validated(self):
        good = os.path.join(self.dir, "good.frames")
        frames_file(good, [bytes(640), b"\xff" * 640], delay=80)
        frames, delay = apply.load_frames(False, good)
        self.assertEqual((len(frames), delay), (2, 0.08))
        self.assertEqual(apply.load_frames(True, good)[0][0], b"\xff" * 640)
        self.assertEqual(apply.load_frames(False, good, delay_override_ms=9999)[1], 0.5)
        for blob in (b"NOTFRAME" + bytes(644), apply.FRAMES_MAGIC + struct.pack("<HH", 3, 100) + bytes(640), b""):
            bad = os.path.join(self.dir, "bad.frames")
            with open(bad, "wb") as f:
                f.write(blob)
            with self.assertRaises(SystemExit):
                apply.load_frames(False, bad)

    def test_sample_frames_keeps_the_ends(self):
        frames = [bytes([i]) for i in range(10)]
        self.assertEqual(apply.sample_frames(frames, 4), [frames[0], frames[3], frames[6], frames[9]])
        self.assertEqual(apply.sample_frames(frames, 20), frames)

    def test_hex_only_reads_the_plugins_own_files(self):
        with self.assertRaises(SystemExit):
            apply.frame_hex("/etc/passwd")
        self.assertEqual(len(apply.frame_hex(str(ROOT / "assets" / "omarchy-oled-128x40.bin"))), 1280)

    def test_device_arguments_are_hidraw_nodes_only(self):
        self.assertEqual(apply.device_arg(["/dev/hidraw7"]), "/dev/hidraw7")
        self.assertIsNone(apply.device_arg([]))
        for bad in (["/etc/passwd"], ["--have"], ["/dev/hidraw1", "x"]):
            with self.assertRaises(SystemExit):
                apply.device_arg(bad)

    def test_only_the_gif_host_over_https(self):
        for url in ("http://www.nlog.us/x.gif", "https://evil.example/x.gif", "https://user@www.nlog.us/x.gif",
                    "file:///etc/passwd"):
            with self.assertRaises(SystemExit):
                apply.fetch_url_to_tmp(url)


class StateCommands(unittest.TestCase):
    def test_settings_round_trip(self):
        self.assertEqual(run("--write-settings", stdin=b'{"source":"info:clock"}').returncode, 0)
        path = os.path.join(STATE, "omarchy", "steelseries-oled.json")
        self.assertEqual(os.stat(path).st_mode & 0o777, 0o600)
        self.assertEqual(json.loads(run("--read-settings").stdout), {"source": "info:clock"})

    def test_settings_must_be_a_json_object_of_sane_size(self):
        for junk in (b"[1,2]", b"not json", b'{"pad":"' + b"x" * (apply.MAX_SETTINGS_BYTES + 10) + b'"}'):
            self.assertNotEqual(run("--write-settings", stdin=junk).returncode, 0)

    def test_a_linked_settings_file_is_refused(self):
        path = os.path.join(STATE, "omarchy", "steelseries-oled.json")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        if os.path.lexists(path):
            os.unlink(path)
        os.symlink("/etc/passwd", path)
        try:
            result = run("--read-settings")
            self.assertEqual(result.returncode, 4)
            self.assertEqual(result.stdout, b"")
        finally:
            os.unlink(path)

    def test_drawing_round_trip_and_have(self):
        out = os.path.join(STATE, "jobs")
        hexframe = ("0f" * 640)
        self.assertEqual(run("--write-drawing", "--out-dir", out, stdin=hexframe.encode()).returncode, 0)
        self.assertEqual(run("--read-drawing", "--out-dir", out).stdout.decode().strip(), hexframe)
        self.assertNotEqual(run("--write-drawing", "--out-dir", out, stdin=b"zz").returncode, 0)
        self.assertEqual(json.loads(run("--have", "--out-dir", out).stdout), {"custom": False, "text": False, "drawing": True})
        self.assertEqual(os.stat(out).st_mode & 0o777, 0o700)

    @unittest.skipUnless(HAVE_MAGICK, "needs ImageMagick")
    def test_render_jobs_write_private_files(self):
        out = os.path.join(STATE, "render")
        result = run("--render-text", "HI", "--style", "static", "--out-dir", out, "--preview-suffix", "2")
        self.assertEqual(json.loads(result.stdout)["type"], "text")
        for name in os.listdir(out):
            self.assertEqual(os.stat(os.path.join(out, name)).st_mode & 0o777, 0o600, name)
        self.assertIn("text-preview-2.gif", os.listdir(out))
        # The label is never read from a file: "@" text is just text.
        self.assertEqual(run("--render-text", "@/etc/hostname", "--out-dir", out).returncode, 0)


def fake_labels():
    """Every string is w x 7 of vertical stripes, w = 6 per character:
    deterministic, no magick needed, and a scroll shows."""
    def render(text, size):
        w = 6 * len(text)
        return w, 7, bytes(1 if (x // 3) % 2 == 0 else 0 for _ in range(7) for x in range(w))
    return apply.Labels(render=render)


def ink_rows(frame):
    rows = set()
    for i in range(apply.OLED_W * apply.OLED_H):
        if frame[i >> 3] & (0x80 >> (i & 7)):
            rows.add(i // apply.OLED_W)
    return rows


class InfoScreens(unittest.TestCase):
    def test_clock(self):
        t = time.struct_time((2026, 10, 19, 13, 52, 0, 0, 292, 0))
        info = apply.Info("clock", labels=fake_labels(), clock=lambda: t)
        frame = info.frame(0)
        self.assertEqual(len(frame), 640)
        self.assertTrue(ink_rows(frame))
        self.assertIn(("13:52", 30), info.labels.cache)
        self.assertIn(("MON 19 OCT", 10), info.labels.cache)
        self.assertTrue(info.still)

    def test_the_clock_wanders_a_little(self):
        positions = set()
        for minute in range(15):
            t = time.struct_time((2026, 10, 19, 13, minute, 0, 0, 292, 0))
            frame = apply.Info("clock", labels=fake_labels(), clock=lambda: t).frame(0)
            positions.add(frame)
        self.assertGreater(len(positions), 3)

    def test_system_reads_fake_proc_and_hwmon(self):
        d = tempfile.mkdtemp(prefix="oled-proc-")
        stat_path, mem_path = os.path.join(d, "stat"), os.path.join(d, "meminfo")
        with open(stat_path, "w") as f:
            f.write("cpu  100 0 100 800 0 0 0 0 0 0\n")
        with open(mem_path, "w") as f:
            f.write("MemTotal:       1000 kB\nMemFree:  100 kB\nMemAvailable:    250 kB\n")
        chip = os.path.join(d, "hwmon", "hwmon3")
        os.makedirs(chip)
        with open(os.path.join(chip, "name"), "w") as f:
            f.write("coretemp\n")
        with open(os.path.join(chip, "temp1_input"), "w") as f:
            f.write("58000\n")
        old = apply.PROC_STAT, apply.PROC_MEMINFO, apply.HWMON_ROOT
        apply.PROC_STAT, apply.PROC_MEMINFO, apply.HWMON_ROOT = stat_path, mem_path, os.path.join(d, "hwmon")
        try:
            self.assertEqual(apply.cpu_times(), (1000, 800))
            self.assertAlmostEqual(apply.memory_used(), 0.75)
            self.assertEqual(apply.cpu_temperature(), 58.0)
            info = apply.Info("system", labels=fake_labels())
            with open(stat_path, "w") as f:
                f.write("cpu  150 0 150 900 0 0 0 0 0 0\n")  # +100 busy, +100 idle: 50%
            info.frame(10.0)
            self.assertAlmostEqual(info.cpu_value, 0.5)
            self.assertIn(("50", 9), info.labels.cache)
            self.assertIn(("75", 9), info.labels.cache)
            self.assertIn(("58", 9), info.labels.cache)
        finally:
            apply.PROC_STAT, apply.PROC_MEMINFO, apply.HWMON_ROOT = old

    def test_media_scrolls_long_titles_and_falls_back_to_the_clock(self):
        info = apply.Info("media", labels=fake_labels())
        idle = info.frame(0)
        self.assertTrue(any(k[1] == 30 for k in info.labels.cache))  # nothing playing: the clock
        self.assertTrue(info.feed(json.dumps({"media": {"title": "a very long title that cannot fit on the screen",
                                                        "artist": "someone", "playing": True, "progress": 0.5}})))
        first, second = info.frame(0), info.frame(0)
        self.assertNotEqual(first, second)
        self.assertNotEqual(first, idle)
        self.assertIn(("A VERY LONG TITLE THAT CANNOT FIT ON THE SCREEN", 10), info.labels.cache)
        self.assertFalse(info.still)

    def test_media_input_is_cleaned(self):
        info = apply.Info("media", labels=fake_labels())
        self.assertFalse(info.feed("not json"))
        self.assertFalse(info.feed("[1]"))
        self.assertTrue(info.feed(json.dumps({"media": {"title": "naïve \u202e<b>", "progress": 7}})))
        self.assertEqual(info.media["title"], "NAVE <B>")
        self.assertEqual(info.media["progress"], -1.0)
        self.assertEqual(apply.clean_label("  a\tb  c ", 10), "A B C")

    def test_preview_is_switched_from_stdin(self):
        info = apply.Info("clock", labels=fake_labels())
        self.assertTrue(info.feed('{"preview": true}'))
        self.assertTrue(info.preview)
        self.assertFalse(info.feed('{"preview": false}'))
        self.assertFalse(info.preview)

    def test_inverted_frames_keep_a_plain_copy_for_the_panel(self):
        info = apply.Info("clock", labels=fake_labels(), invert=True)
        frame = info.frame(0)
        self.assertEqual(apply.invert_payload(frame), info.plain)


class Streaming(unittest.TestCase):
    def test_live_frames_from_stdin_reach_the_device(self):
        sent = []
        real = apply.find_oled_hidraw, apply.open_device, apply.send_feature_fd
        apply.find_oled_hidraw = lambda: "/dev/hidraw99"
        apply.open_device = lambda path: os.open("/dev/null", os.O_RDWR)
        apply.send_feature_fd = lambda fd, payload: sent.append(payload)
        r, w = os.pipe()
        out = []
        try:
            stdin = apply.StdinLines(r)
            source = apply.Live(bytes(640), invert=False)
            t = threading.Thread(target=lambda: out.append(apply.watch(source, stdin)), daemon=True)
            import io, contextlib
            with contextlib.redirect_stdout(io.StringIO()):
                t.start()
                time.sleep(0.2)
                os.write(w, (("ff" * 640) + "\n").encode())
                os.write(w, b"junk line\n")
                time.sleep(0.3)
                os.close(w)
                t.join(3)
        finally:
            apply.find_oled_hidraw, apply.open_device, apply.send_feature_fd = real
            os.close(r)
        self.assertFalse(t.is_alive(), "watch did not stop when stdin closed")
        self.assertEqual(sent[0], bytes(640))
        self.assertIn(b"\xff" * 640, sent)

    def test_stdin_lines_are_capped(self):
        r, w = os.pipe()
        lines = apply.StdinLines(r)
        os.write(w, b"x" * (apply.MAX_STDIN_LINE + 10) + b"\nok\n")
        got = []
        for _ in range(5):
            got += lines.wait(0.05)
        self.assertEqual(got, ["ok"])
        os.close(w)
        lines.wait(0.05)
        self.assertTrue(lines.closed)
        os.close(r)

    def test_a_still_is_resent_slowly(self):
        self.assertEqual(apply.Frames([bytes(640)] * 3, 0.1).tick, apply.STILL_RESEND_S)
        self.assertEqual(apply.Frames([bytes(640), b"\x01" * 640], 0.1).tick, 0.1)


class MakeGif(unittest.TestCase):
    def test_pack_round_trip_and_animation(self):
        import make_gif
        blob = (ROOT / "assets" / "omarchy-oled-128x40.bin").read_bytes()
        pix = make_gif.unpack(blob)
        self.assertEqual(make_gif.pack(pix), blob)
        frames, delay = apply.animate_bitmap(blob, "spin")
        self.assertTrue(frames and all(len(f) == 640 for f in frames))
        self.assertEqual(apply.animate_bitmap(blob, "static")[0], [blob])


class TextEffects(unittest.TestCase):
    """The logo's screensaver effects on custom text (make_screensaver)."""

    def setUp(self):
        import make_gif
        import make_screensaver
        self.gif, self.ms = make_gif, make_screensaver
        self.blob = (ROOT / "assets" / "omarchy-oled-128x40.bin").read_bytes()

    def test_styles_are_checked(self):
        for style in ("spin", "screensaver", "screensaver:decrypt", "screensaver:wipe"):
            self.assertEqual(apply.text_style(style), style)
        for style in ("screensaver:", "screensaver:nope", "screensaver:../x", "Screensaver", ""):
            self.assertEqual(apply.text_style(style), "typewriter")

    def test_effect_list_matches_the_service(self):
        qml = (ROOT / "Service.qml").read_text(encoding="utf-8")
        block = qml[qml.index("screensaverEffects: ["):]
        names = __import__("re").findall(r'"([a-z]+)"', block[:block.index("]")])
        self.assertEqual(names, self.ms.EFFECTS)

    def test_half_block_art_maps_back_pixel_for_pixel(self):
        # The wordmark moved down to the bottom edge: the art starts at the
        # cell row of its first inked pixel row.
        pix = self.gif.unpack(self.blob)
        pix = [[0] * 128] * 6 + pix[:-6]
        blob = self.gif.pack(pix)
        art, top = self.ms.bitmap_art(pix)
        self.assertEqual(top, next(y for y in range(40) if any(pix[y])) // 2)
        lines = art.rstrip("\n").split("\n")
        self.assertEqual(lines[0], lines[0].rstrip())
        grid = [[(ch, 1.0) for ch in line] for line in lines]
        self.assertEqual(self.ms.rasterise(grid, 128, 20, top), blob)
        with self.assertRaises(SystemExit):
            self.ms.bitmap_art(self.gif.blank())

    def test_painted_frames_are_split_and_parsed(self):
        on, dim = "\x1b[38;2;255;255;255m", "\x1b[38;2;20;20;20m"
        frame = on + "\u2588" + "\x1b[0m" + dim + "\u2588" + "\x1b[0m" + "\r\n" + " " + on + "\u2580"
        raw = ("\x1b[?25l" + "\x1b8\x1b7\x1b[2A" + frame).encode() * 2
        chunks = self.ms.split_frames(raw)
        self.assertEqual(len(chunks), 2)
        grid = self.ms.parse_frame(chunks[0], 2)
        self.assertEqual([c for c, _ in grid[0]], ["\u2588", "\u2588"])
        self.assertLess(grid[0][1][1], self.ms.LUMA_ON)
        pix = self.gif.unpack(self.ms.rasterise(grid, 128, 20))
        self.assertEqual((pix[0][0], pix[1][0], pix[0][1], pix[2][1], pix[3][1]), (1, 1, 0, 1, 0))

    def test_resampling_keeps_wall_clock_length(self):
        idx = self.ms.resample_indices(1200)
        self.assertEqual(len(idx), int(self.ms.MAX_EFFECT_S * 1000 / self.ms.DELAY_MS))
        self.assertEqual((idx[0], idx[-1]), (0, 1199))
        self.assertEqual(len(self.ms.resample_indices(120)), 16)

    @unittest.skipUnless(os.access("/usr/bin/ttfx", os.X_OK), "needs ttfx")
    def test_an_effect_settles_on_the_bitmap(self):
        frames, delay, preview = apply.animate_screensaver(self.blob, "screensaver:wipe")
        self.assertEqual(delay, self.ms.DELAY_MS)
        self.assertEqual(preview, frames)
        self.assertGreater(len(frames), len(self.ms.hold_last(self.blob)))
        self.assertNotEqual(frames[0], self.blob)
        self.assertEqual(frames[-len(self.ms.hold_last(self.blob)):], self.ms.hold_last(self.blob))


# --- the Peak takeover ---------------------------------------------------------------


def fake_glyph_render(texts, size):
    """Every character a 4-wide block (a "1" 2 wide) at the full line height
    `size`, inked on rows 1..size-2 but for one row that depends on the
    character; "(" and ")" are missing from the font. "00" and "0 0" make
    the gap 1 px and a space 3 px."""
    out = {}
    for text in texts:
        if text in ("(", ")"):
            continue
        w = {"00": 9, "0 0": 11, "1": 2}.get(text, 4)
        hole = 2 + ord(text[0]) % (size - 4)
        out[text] = (w, size, bytes(1 if 1 <= y <= size - 2 and y != hole else 0 for y in range(size) for _ in range(w)))
    return out


def fake_fonts():
    return {role: apply.Glyphs(size, chars, render=fake_glyph_render) for role, (size, chars) in apply.PEAK_TYPE.items()}


class FakeLink:
    """Stands in for PeakLink: hands over whatever the test queued."""

    def __init__(self, up=True):
        self.up = up
        self.queue = []

    def poll(self, now):
        out, self.queue = self.queue, []
        return out


PROFILES = [{"index": 0, "name": "HASH (FlavorSaver)"}, {"index": 2, "name": "High"}]


def peak_reading(state, temp_f=500.0, elapsed=5.0, total=20.0, **extra):
    number = isinstance(temp_f, (int, float)) and not isinstance(temp_f, bool)
    status = {"connected": True, "operating_state_id": state, "operating_state": "Preheating",
              "heater_temp_f": temp_f, "heater_temp_c": (temp_f - 32) * 5 / 9 if number else None,
              "state_elapsed_s": elapsed, "state_total_s": total, "current_profile": 2, "profiles": PROFILES}
    status.update(extra)
    return status


def make_peak(link=None, start=0.0, **kwargs):
    return apply.Peak(link=link or FakeLink(), fonts=fake_fonts(), clock=lambda: start, **kwargs)


def fed(peak, status, now):
    """peak after one status arrives at `now`, polled with stdout held."""
    import contextlib
    import io
    peak.link.queue.append(status)
    with contextlib.redirect_stdout(io.StringIO()):
        peak.poll(now)
    return peak


def lit(frame, x, y):
    i = y * apply.OLED_W + x
    return bool(frame[i >> 3] & (0x80 >> (i & 7)))


class Glyphs(unittest.TestCase):
    def test_spacing_space_and_tabular_digits(self):
        g = apply.Glyphs(10, render=fake_glyph_render)
        self.assertEqual((g.text("10")[0], g.text("10", tabular=True)[0]), (2 + 1 + 4, 4 + 1 + 4))
        self.assertEqual(g.text("A B")[0], 4 + 3 + 4)
        self.assertEqual((g.h, g.cap_top, g.cap_h), (10, 1, 8))
        w, h, bits = g.text("0")
        self.assertEqual(len(bits), w * h)

    def test_missing_characters_are_left_out_and_names_fit(self):
        g = apply.Glyphs(10, render=fake_glyph_render)
        self.assertEqual(g.text("(A)")[0], 4)
        self.assertEqual(g.fit("HASH (FLAVORSAVER)", 40), "HASH")
        self.assertEqual(g.fit("HASH (FLAVORSAVER)", 200), "HASH FLAVORSAVER")
        self.assertEqual(g.fit("ABCDEFGHIJ", 20), "ABCD")
        self.assertEqual(g.fit("", 20), "")

    def test_a_failed_render_draws_nothing(self):
        def broken(texts, size):
            raise OSError("no magick")
        g = apply.Glyphs(10, render=broken)
        self.assertEqual(g.text("530")[0], 0)
        self.assertEqual(g.fit("HIGH", 50), "")

    @unittest.skipUnless(HAVE_MAGICK, "needs ImageMagick")
    def test_the_real_font_keeps_its_digits_and_baseline(self):
        for role, (size, chars) in apply.PEAK_TYPE.items():
            g = apply.Glyphs(size, chars)
            g.load()
            wanted = set(chars) & set("0123456789:FCHEATINGRDYOL")
            self.assertEqual(wanted - set(g.shapes), set(), role)
            self.assertEqual({shape[1] for shape in g.shapes.values()}, {g.h}, role)
            self.assertTrue(0 < g.cap_h <= g.h, role)


class PeakScreen(unittest.TestCase):
    def test_heating_ready_and_cooling(self):
        frames = {}
        for state, word in ((7, "HEATING"), (8, "READY"), (9, "COOLING")):
            peak = fed(make_peak(), peak_reading(state, ui={"units": "F"}), 0.0)
            view = peak.view(0.0)
            self.assertEqual(view, (word, state == 8, "500", "F", "0:15", round(apply.PEAK_BAR_W * 0.25), "HIGH"))
            frame = peak.frame(0.0)
            self.assertEqual(len(frame), apply.PAYLOAD_BYTES)
            frames[state] = frame
            # The bar: outline along the bottom, a quarter filled.
            self.assertTrue(lit(frame, 0, apply.OLED_H - 1) and lit(frame, 127, apply.OLED_H - 6))
            self.assertTrue(lit(frame, 2, apply.OLED_H - 4) and lit(frame, 32, apply.OLED_H - 4))
            self.assertFalse(lit(frame, 40, apply.OLED_H - 4))
            # READY is reversed out of a block; the others aren't.
            self.assertEqual(lit(frame, 0, 0), state == 8)
        self.assertEqual(len(set(frames.values())), 3)

    def test_units_rounding_and_the_remembered_unit(self):
        peak = fed(make_peak(), peak_reading(7, temp_f=529.5, heater_temp_c=211.5, ui={"units": "C"}), 0.0)
        self.assertEqual(peak.view(0.0)[2:4], ("212", "C"))
        # Status events don't carry the shell's settings: the unit stays.
        fed(peak, peak_reading(7, temp_f=529.5, heater_temp_c=276.4), 0.5)
        self.assertEqual(peak.view(0.5)[2:4], ("276", "C"))
        peak = fed(make_peak(), peak_reading(7, temp_f=529.5), 0.0)
        self.assertEqual(peak.view(0.0)[2:4], ("530", "F"))

    def test_the_countdown_ticks_between_readings(self):
        peak = fed(make_peak(), peak_reading(8, elapsed=5.0, total=200.0), 0.0)
        peak.frame(0.0)
        self.assertEqual(peak.view(0.0)[4], "3:15")
        # Nothing new to draw until the next whole second.
        self.assertFalse(peak.poll(0.4))
        self.assertTrue(peak.poll(1.0))
        self.assertEqual(peak.view(1.0)[4], "3:14")
        self.assertEqual(peak.view(300.0)[4:6], ("0:00", apply.PEAK_BAR_W))

    def test_a_late_reading_does_not_count_back_up(self):
        peak = fed(make_peak(), peak_reading(7, elapsed=5.0), 0.0)
        # 1.2 s later the Peak still says 5 s in (it counts whole seconds).
        fed(peak, peak_reading(7, elapsed=5.0), 1.2)
        self.assertEqual(peak.view(1.2)[4], "0:14")
        # A boost lengthens the phase, a new phase starts over: both count.
        fed(peak, peak_reading(7, elapsed=6.0, total=35.0), 2.0)
        self.assertEqual(peak.view(2.0)[4], "0:29")
        fed(peak, peak_reading(8, elapsed=0.0, total=30.0), 3.0)
        self.assertEqual(peak.view(3.0)[4], "0:30")

    def test_missing_and_strange_fields(self):
        reading = peak_reading(7, temp_f=None, elapsed=None)
        peak = fed(make_peak(), reading, 0.0)
        self.assertEqual(peak.view(0.0)[2:6], (None, "F", None, None))
        self.assertEqual(len(peak.frame(0.0)), apply.PAYLOAD_BYTES)
        for bad in (True, float("nan"), float("inf"), 1e9, "530", [530]):
            peak = fed(make_peak(), peak_reading(7, temp_f=bad, total=bad), 0.0)
            self.assertEqual(peak.view(0.0)[2], None, bad)
            self.assertEqual(peak.view(0.0)[4], None, bad)
        peak = fed(make_peak(), peak_reading(True), 0.0)
        self.assertEqual(peak.view(0.0)[0], "PREHEATING")
        # No profile list in a reading keeps the name; a bad index blanks it.
        peak = fed(make_peak(), peak_reading(7), 0.0)
        fed(peak, peak_reading(7, profiles=None), 0.5)
        self.assertEqual(peak.view(0.5)[6], "HIGH")
        fed(peak, peak_reading(7, current_profile=True), 1.0)
        self.assertEqual(peak.view(1.0)[6], "")

    def test_offline_no_peak_idle_and_done(self):
        self.assertEqual(make_peak(FakeLink(up=False)).view(0.0)[0], "OFFLINE")
        self.assertEqual(make_peak().view(0.0)[0], "PEAK")
        self.assertEqual(fed(make_peak(), peak_reading(-1, connected=False), 0.0).view(0.0)[0], "NO PEAK")
        idle = peak_reading(5, elapsed=None, operating_state="Idle")
        self.assertEqual(fed(make_peak(), idle, 0.0).view(0.0)[0], "IDLE")
        peak = fed(make_peak(), peak_reading(9), 0.0)
        self.assertEqual(fed(peak, idle, 1.0).view(1.0)[:5], ("DONE", False, "500", "F", None))

    def test_inverted_frames_keep_a_plain_copy_for_the_panel(self):
        peak = fed(make_peak(invert=True), peak_reading(8), 0.0)
        frame = peak.frame(0.0)
        self.assertEqual(apply.invert_payload(frame), peak.plain)

    def test_preview_is_switched_from_stdin(self):
        peak = make_peak()
        self.assertTrue(peak.feed('{"preview": true}'))
        self.assertTrue(peak.preview)
        self.assertFalse(peak.feed('{"media": {}}'))
        self.assertFalse(peak.feed("ff" * 640))


class PeakHandBack(unittest.TestCase):
    """The takeover ends by itself: ~20 s out of a heat cycle, or ~20 s
    without the daemon, and the stream stops if the shell doesn't act."""

    def poll(self, peak, now):
        import contextlib
        import io
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            peak.poll(now)
        return [json.loads(line) for line in out.getvalue().splitlines()]

    def test_twenty_seconds_out_of_a_cycle(self):
        peak = make_peak()
        idle = peak_reading(5, elapsed=None)
        fed(peak, peak_reading(9), 1.0)
        for t in (2.0, 10.0, 20.0):
            fed(peak, idle, t)
        self.assertEqual(self.poll(peak, 20.9), [])
        self.assertEqual(self.poll(peak, 21.0), [{"type": "peak", "state": "over", "reason": "not-heating"}])
        self.assertEqual(self.poll(peak, 22.0), [])
        self.assertFalse(peak.done)
        self.poll(peak, 21.0 + apply.PEAK_HANDBACK_S)
        self.assertTrue(peak.done)

    def test_twenty_seconds_without_the_daemon(self):
        peak = make_peak(FakeLink(up=False))
        self.assertEqual(self.poll(peak, 19.9), [])
        self.assertEqual(self.poll(peak, 20.0), [{"type": "peak", "state": "over", "reason": "unreachable"}])
        # Heard from once, then silent (a hung daemon) is the same.
        peak = make_peak()
        fed(peak, peak_reading(8), 1.0)
        self.assertEqual(self.poll(peak, 21.0)[0]["reason"], "unreachable")

    def test_a_long_session_keeps_it(self):
        peak = make_peak()
        for t in range(0, 120, 5):
            fed(peak, peak_reading(8, elapsed=float(t), total=200.0), float(t))
            self.assertEqual(self.poll(peak, t + 4.9), [])
        self.assertFalse(peak.done)


class FakeDaemon:
    """QuickPuff's socket, played from a script: one list of byte chunks per
    connection it accepts (CLOSE hangs up), recording what each client sent."""

    CLOSE = object()

    def __init__(self, path, scripts):
        self.path, self.scripts = path, scripts
        self.received = []
        self.server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.server.bind(path)
        self.server.listen(4)
        self.server.settimeout(5)
        self.conns = []
        self.thread = threading.Thread(target=self.serve, daemon=True)
        self.thread.start()

    def serve(self):
        for script in self.scripts:
            try:
                conn, _ = self.server.accept()
            except OSError:
                return
            self.conns.append(conn)
            conn.settimeout(2)
            try:
                self.received.append(conn.recv(4096))
                for chunk in script:
                    if chunk is self.CLOSE:
                        conn.close()
                        break
                    conn.sendall(chunk)
            except OSError:
                pass

    def stop(self):
        self.server.close()
        for conn in self.conns:
            conn.close()
        self.thread.join(3)


def reply(status):
    return (json.dumps({"id": 1, "ok": True, "result": status}) + "\n").encode()


def event(name, data):
    return (json.dumps({"event": name, "data": data}) + "\n").encode()


class PeakLinkSocket(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="oled-qp-")
        self.path = os.path.join(self.dir, "quickpuff.sock")
        self.daemon = None
        # Reconnect quickly here; the backoff itself has its own test.
        self.retry = apply.PEAK_RETRY_S
        apply.PEAK_RETRY_S = (0.01, 0.02)

    def tearDown(self):
        apply.PEAK_RETRY_S = self.retry
        if self.daemon:
            self.daemon.stop()

    def serve(self, *scripts):
        self.daemon = FakeDaemon(self.path, list(scripts))

    def link(self, path=None, **kwargs):
        link = apply.PeakLink(path or self.path, **kwargs)
        self.addCleanup(link.close)
        return link

    def collect(self, link, want, timeout=4.0):
        got, end = [], time.monotonic() + timeout
        while len(got) < want and time.monotonic() < end:
            got += link.poll(time.monotonic())
            time.sleep(0.02)
        return got

    def test_asks_once_then_reads_status_events(self):
        big = peak_reading(7, telemetry={"pad": "x" * 6000})
        self.serve([reply(peak_reading(7, ui={"units": "C"})), event("notify", {"title": "Ready"}),
                    event("status", big)])
        link = self.link()
        got = self.collect(link, 2)
        self.assertEqual([s["operating_state_id"] for s in got], [7, 7])
        self.assertEqual(got[0]["ui"], {"units": "C"})
        self.assertEqual(len(got[1]["telemetry"]["pad"]), 6000)
        self.assertTrue(link.up)
        self.assertEqual(self.daemon.received, [apply.PEAK_STATUS_REQUEST])

    def test_bad_lines_drop_the_link_and_it_reconnects(self):
        bad = [b"not json\n", b"[1, 2]\n", b'{"id": 1, "ok": true, "result": 5}\n', b'{"hello": 1}\n',
               b'{"event": "status", "data": null}\n', b"\xff\xfe\n"]
        self.serve(*([line] for line in bad), [reply(peak_reading(8))])
        link = self.link()
        got = self.collect(link, 1, timeout=30)
        self.assertEqual([s["operating_state_id"] for s in got], [8])
        self.assertEqual(self.daemon.received, [apply.PEAK_STATUS_REQUEST] * (len(bad) + 1))
        self.assertEqual(link.failures, 0)

    def test_an_error_reply_is_ignored_not_fatal(self):
        self.serve([b'{"id": 1, "ok": false, "error": "busy"}\n', event("status", peak_reading(9))])
        link = self.link()
        self.assertEqual([s["operating_state_id"] for s in self.collect(link, 1)], [9])
        self.assertEqual(len(self.daemon.received), 1)

    def test_oversize_lines_drop_the_link(self):
        cap = 4096
        long_line = event("status", peak_reading(7, pad="x" * cap))
        no_newline = b"{" + b"x" * (cap * 3)
        self.serve([long_line], [no_newline], [reply(peak_reading(8))])
        link = self.link(max_line=cap)
        got = self.collect(link, 1, timeout=10)
        self.assertEqual([s["operating_state_id"] for s in got], [8])
        self.assertEqual(len(self.daemon.received), 3)

    def test_a_hang_up_reconnects(self):
        self.serve([reply(peak_reading(7)), FakeDaemon.CLOSE], [reply(peak_reading(8))])
        link = self.link()
        got = self.collect(link, 2, timeout=10)
        self.assertEqual([s["operating_state_id"] for s in got], [7, 8])

    def test_a_missing_socket_backs_off(self):
        apply.PEAK_RETRY_S = self.retry
        link = self.link()
        self.assertEqual(link.poll(0.0), [])
        self.assertFalse(link.up)
        self.assertEqual(link.retry_at, apply.PEAK_RETRY_S[0])
        link.poll(0.1)
        self.assertEqual(link.failures, 1)
        for _ in range(10):
            link.poll(link.retry_at)
        self.assertEqual(link.failures, 11)
        now = link.retry_at
        link.poll(now)
        self.assertEqual(link.retry_at, now + apply.PEAK_RETRY_S[-1])

    def test_only_a_socket_of_ours_with_our_daemon_behind_it(self):
        self.serve([reply(peak_reading(7))])
        other = os.path.join(self.dir, "link.sock")
        os.symlink(self.path, other)
        self.assertFalse(self.link(other).connect())
        plain = os.path.join(self.dir, "plain")
        with open(plain, "w") as f:
            f.write("{}")
        self.assertFalse(self.link(plain).connect())
        real = apply.peer_uid
        apply.peer_uid = lambda sock: os.geteuid() + 1
        try:
            self.assertFalse(self.link().connect())
        finally:
            apply.peer_uid = real
        self.assertTrue(self.link().connect())

    def test_the_socket_path(self):
        old = {k: os.environ.get(k) for k in ("QUICKPUFF_SOCKET", "XDG_RUNTIME_DIR")}
        try:
            os.environ.pop("QUICKPUFF_SOCKET", None)
            os.environ["XDG_RUNTIME_DIR"] = "/run/user/4242"
            self.assertEqual(apply.quickpuff_socket(), "/run/user/4242/quickpuff.sock")
            os.environ["QUICKPUFF_SOCKET"] = self.path
            self.assertEqual(apply.quickpuff_socket(), self.path)
        finally:
            for k, v in old.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v


class PeakStream(unittest.TestCase):
    def test_watch_draws_the_peak_then_hands_back_and_stops(self):
        sent = []
        real = (apply.find_oled_hidraw, apply.open_device, apply.send_feature_fd,
                apply.PEAK_GIVE_UP_S, apply.PEAK_HANDBACK_S)
        apply.find_oled_hidraw = lambda: "/dev/hidraw99"
        apply.open_device = lambda path: os.open("/dev/null", os.O_RDWR)
        apply.send_feature_fd = lambda fd, payload: sent.append(payload)
        apply.PEAK_GIVE_UP_S, apply.PEAK_HANDBACK_S = 0.6, 0.3
        link = FakeLink()
        link.queue.append(peak_reading(7))
        peak = apply.Peak(link=link, fonts=fake_fonts())
        r, w = os.pipe()
        import contextlib
        import io
        out = io.StringIO()
        try:
            stdin = apply.StdinLines(r)
            t = threading.Thread(target=apply.watch, args=(peak, stdin), daemon=True)
            with contextlib.redirect_stdout(out):
                t.start()
                os.write(w, b'{"preview": true}\n')
                t.join(5)
        finally:
            (apply.find_oled_hidraw, apply.open_device, apply.send_feature_fd,
             apply.PEAK_GIVE_UP_S, apply.PEAK_HANDBACK_S) = real
            os.close(w)
            os.close(r)
        self.assertFalse(t.is_alive(), "watch did not stop after handing back")
        self.assertTrue(sent)
        lines = [json.loads(line) for line in out.getvalue().splitlines()]
        self.assertIn({"type": "peak", "state": "over", "reason": "unreachable"}, lines)
        self.assertTrue(any(line.get("type") == "frame" for line in lines))

    def test_the_command_line(self):
        self.assertIn(b"--info needs --watch", run("--info", "peak").stderr)
        self.assertIn(b"peak", run("--watch", "--info", "nope").stderr)


class PeakService(unittest.TestCase):
    """Service.qml's half of the takeover, read as text."""

    def setUp(self):
        self.qml = (ROOT / "Service.qml").read_text(encoding="utf-8")

    def block(self, start):
        body = self.qml[self.qml.index(start):]
        return body[:body.index("\n  }\n")]

    def test_the_takeover_is_never_saved(self):
        self.assertNotIn("peak", self.block("function settingsJson()").lower())
        self.assertNotIn("peak", self.block("function loadSettings(raw)").lower())

    def test_it_runs_and_ends_the_way_apply_py_speaks(self):
        self.assertIn('cmd.push("--info", "%s")' % apply.PEAK_MODE, self.block("function watchCommand()"))
        handle = self.block("function handleLine(line)")
        self.assertIn('msg.type === "peak"', handle)
        self.assertIn('msg.state === "over"', handle)
        self.assertIn("function peak(state: string): string", self.qml)


if __name__ == "__main__":
    unittest.main()
