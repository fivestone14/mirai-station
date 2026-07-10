"""Tests for claude_cli.extract_json — the JSON extractor every parse_json=True
reasoning call relies on (the macro-brief and the R14 insight analyzer both emit
deeply-nested schemas, which the old regex mis-parsed into a wrong inner object)."""
from __future__ import annotations

import unittest

from watch import claude_cli


class TestExtractJson(unittest.TestCase):
    def test_deeply_nested_object_returns_top_level(self):
        # The macro-brief shape: 3 levels of nesting. The old regex returned the
        # first innermost object ({"direction":...}); the fix returns the whole thing.
        text = ('{"overall": {"direction": -0.35, "magnitude": 0.85, "confidence": 0.4}, '
                '"sectors": {"semis": {"direction": -0.7, "magnitude": 1.2}}, '
                '"reasoning": "semis lead lower on an Asian chip rout"}')
        obj = claude_cli.extract_json(text)
        self.assertEqual(set(obj), {"overall", "sectors", "reasoning"})
        self.assertEqual(obj["overall"]["direction"], -0.35)
        self.assertEqual(obj["sectors"]["semis"]["magnitude"], 1.2)
        self.assertTrue(obj["reasoning"])

    def test_fenced_and_prose_wrapped(self):
        body = '{"a": {"b": {"c": 1}}, "d": 2}'
        self.assertEqual(claude_cli.extract_json(f"Here you go:\n```json\n{body}\n```"),
                         {"a": {"b": {"c": 1}}, "d": 2})
        self.assertEqual(claude_cli.extract_json(f"prose first. {body} trailing"),
                         {"a": {"b": {"c": 1}}, "d": 2})

    def test_first_complete_object_wins_and_bad_input(self):
        self.assertEqual(claude_cli.extract_json('{"x":1} {"y":2}'), {"x": 1})
        self.assertIsNone(claude_cli.extract_json(""))
        self.assertIsNone(claude_cli.extract_json("no json here at all"))
        self.assertIsNone(claude_cli.extract_json("{not valid json"))

    def test_arrays_and_scalars_inside(self):
        obj = claude_cli.extract_json('{"list": [1, 2, {"k": "v"}], "n": -0.5, "ok": true}')
        self.assertEqual(obj["list"][2]["k"], "v")
        self.assertEqual(obj["n"], -0.5)
        self.assertTrue(obj["ok"])


class TestCommandFlags(unittest.TestCase):
    def test_scoped_readonly_flags_are_passed(self):
        import subprocess
        captured = {}

        def fake_run(cmd, **kw):
            captured["cmd"] = cmd
            return type("R", (), {"returncode": 0, "stdout": '{"ok": true}', "stderr": ""})()

        real, subprocess.run = subprocess.run, fake_run
        try:
            claude_cli.call("hi", model="haiku",
                            allowed_tools=["mcp__market-research", "WebSearch"],
                            disallowed_tools=["Bash", "Write"],
                            permission_mode="bypassPermissions")
        finally:
            subprocess.run = real
        cmd = captured["cmd"]
        self.assertEqual(cmd[cmd.index("--allowedTools") + 1], "mcp__market-research,WebSearch")
        self.assertEqual(cmd[cmd.index("--disallowedTools") + 1], "Bash,Write")
        self.assertEqual(cmd[cmd.index("--permission-mode") + 1], "bypassPermissions")

    def test_flags_omitted_when_not_set(self):
        import subprocess
        captured = {}

        def fake_run(cmd, **kw):
            captured["cmd"] = cmd
            return type("R", (), {"returncode": 0, "stdout": "{}", "stderr": ""})()

        real, subprocess.run = subprocess.run, fake_run
        try:
            claude_cli.call("hi", model="haiku")  # tool-less reasoning
        finally:
            subprocess.run = real
        cmd = captured["cmd"]
        self.assertNotIn("--disallowedTools", cmd)
        self.assertNotIn("--permission-mode", cmd)


if __name__ == "__main__":
    unittest.main()
