#!/usr/bin/python3 -I
"""Static checks that keep the plugin inside the marketplace's security
rules: plain-text QML sinks, absolute argv-only processes, no shell strings,
no invisible characters, a valid manifest.

    python3 -I tests/test_hardening.py
"""

import json
import pathlib
import re
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
SKIP = {".git", "__pycache__", "node_modules"}


def sources(*suffixes, tests=True):
    for path in sorted(ROOT.rglob("*")):
        parts = set(path.relative_to(ROOT).parts)
        if not path.is_file() or path.suffix not in suffixes or SKIP & parts:
            continue
        if tests or "tests" not in parts:
            yield path


def blocks(text, type_name):
    """The body of every `TypeName {` block, braces matched."""
    for match in re.finditer(r"(?<![\w.])" + type_name + r"\s*\{", text):
        depth, i = 1, match.end()
        while depth and i < len(text):
            depth += {"{": 1, "}": -1}.get(text[i], 0)
            i += 1
        yield text.count("\n", 0, match.start()) + 1, text[match.end():i - 1]


def own_properties(body):
    """The block's text with nested child blocks removed."""
    out, depth = [], 0
    for ch in body:
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
        elif depth == 0:
            out.append(ch)
    return "".join(out)


# Built from code points so this file holds none of them itself.
INVISIBLE = re.compile("[%s]" % "".join(
    "%s-%s" % (chr(a), chr(b)) for a, b in (
        (0x061C, 0x061C), (0x200B, 0x200F), (0x202A, 0x202E), (0x2060, 0x2069), (0xFEFF, 0xFEFF),
        (0xE000, 0xF8FF), (0xF0000, 0x10FFFF),
    )
))


class Hardening(unittest.TestCase):
    def test_every_text_is_plain(self):
        missing = []
        for path in sources(".qml"):
            text = path.read_text()
            for line, body in blocks(text, "Text"):
                if "textFormat: Text.PlainText" not in own_properties(body):
                    missing.append(f"{path.relative_to(ROOT)}:{line}")
        self.assertEqual(missing, [], "Text without textFormat: Text.PlainText")

    def test_no_whole_output_collectors_or_shell_strings(self):
        for path in sources(".qml"):
            text = path.read_text()
            self.assertNotIn("StdioCollector", text, path.name)
            self.assertIsNone(re.search(r'"(?:ba)?sh"\s*,\s*"-l?c"', text), path.name)

    def test_processes_start_absolute_programs(self):
        for path in sources(".qml"):
            text = path.read_text()
            for match in re.finditer(r"(?:command\s*[:=]|execDetached\()\s*\[\s*([^\],\s]+)", text):
                first = match.group(1)
                line = text.count("\n", 0, match.start()) + 1
                self.assertTrue(first.startswith('"/'), f"{path.name}:{line} starts with {first}")

    def test_python_never_uses_a_shell(self):
        for path in sources(".py", tests=False):
            text = path.read_text()
            self.assertNotIn("shell=True", text, path.name)
            self.assertNotIn("os.system(", text, path.name)
            self.assertNotIn("os.popen(", text, path.name)

    def test_helpers_run_isolated(self):
        for path in sources(".py", tests=False):
            first = path.read_text().splitlines()[0]
            if first.startswith("#!"):
                self.assertIn(" -I", first, path.name)

    def test_no_invisible_characters_in_code(self):
        found = []
        for path in sources(".qml", ".js", ".py", ".sh", ".mjs"):
            for n, line in enumerate(path.read_text().splitlines(), 1):
                if INVISIBLE.search(line):
                    found.append(f"{path.relative_to(ROOT)}:{n}")
        self.assertEqual(found, [], "write glyphs and controls as escapes")

    def test_manifest(self):
        manifest = json.loads((ROOT / "manifest.json").read_text())
        self.assertRegex(manifest["version"], r"^\d+\.\d+\.\d+$")
        for entry in manifest["entryPoints"].values():
            self.assertTrue((ROOT / entry).is_file(), entry)


if __name__ == "__main__":
    unittest.main()
