#!/usr/bin/python3 -I
"""Tests for apply.py: state files, frame files, the info screens and the
streaming loop. No keyboard is needed: the device is a recorder, /proc and
/sys are fake files, and the state lives in a throwaway XDG_STATE_HOME.

    python3 -I tests/test_apply.py
"""

import importlib.util
import json
import os
import pathlib
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


if __name__ == "__main__":
    unittest.main()
