"""Hidden projects: settings storage, project name resolution, CLI behaviour."""

import contextlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from ai_usage import config
from ai_usage.cli import main, resolve_project
from tests.test_logs import assistant, line

PROJECTS = ["~/dev/acme/encore", "~/dev/acme/encore/apps/web", "~/dev/pet/ai-usage", "/srv/other"]


class TempEnv(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        env = mock.patch.dict(os.environ, {
            "HOME": str(self.tmp / "home"),
            "XDG_CONFIG_HOME": str(self.tmp / "config"),
            "XDG_CACHE_HOME": str(self.tmp / "cache"),
            "CLAUDE_CONFIG_DIR": str(self.tmp / "claude"),
        })
        env.start()
        self.addCleanup(env.stop)


class ConfigTest(TempEnv):
    def test_default_empty(self):
        self.assertEqual(config.hidden_projects(), set())

    def test_hide_unhide_and_keep_other_settings(self):
        config.save({"something_else": 1})
        config.set_hidden("~/a", True)
        config.set_hidden("~/b", True)
        config.set_hidden("~/a", False)
        self.assertEqual(config.hidden_projects(), {"~/b"})
        self.assertEqual(config.load()["something_else"], 1)
        self.assertEqual(config.unhide_all(), 1)
        self.assertEqual(config.hidden_projects(), set())

    def test_corrupt_file_is_ignored(self):
        config.config_file().parent.mkdir(parents=True)
        config.config_file().write_text("{nope")
        self.assertEqual(config.hidden_projects(), set())
        config.config_file().write_text('{"hidden_projects": "not a list"}')
        self.assertEqual(config.hidden_projects(), set())


class ResolveTest(TempEnv):
    def test_exact_and_home_normalization(self):
        self.assertEqual(resolve_project("~/dev/pet/ai-usage/", PROJECTS), "~/dev/pet/ai-usage")
        home = os.environ["HOME"]
        self.assertEqual(resolve_project(f"{home}/dev/pet/ai-usage", PROJECTS), "~/dev/pet/ai-usage")

    def test_unique_substring_and_basename(self):
        self.assertEqual(resolve_project("AI-USAGE", PROJECTS), "~/dev/pet/ai-usage")
        # "encore" is in two names, but only one ends with it.
        self.assertEqual(resolve_project("encore", PROJECTS), "~/dev/acme/encore")

    def test_ambiguous_and_unknown(self):
        with self.assertRaisesRegex(ValueError, "several projects"):
            resolve_project("acme", PROJECTS)
        with self.assertRaisesRegex(ValueError, "No project matches"):
            resolve_project("zzz", PROJECTS)
        self.assertEqual(resolve_project("~/future/thing", PROJECTS, allow_new_path=True), "~/future/thing")


class CliTest(TempEnv):
    def setUp(self):
        super().setUp()
        root = self.tmp / "claude" / "projects"
        for i, cwd in enumerate(["/w/public", "/w/secret-client"]):
            d = root / cwd.replace("/", "-")
            d.mkdir(parents=True)
            (d / "s.jsonl").write_text(line(assistant(f"m{i}", f"r{i}", 10 * (i + 1), cwd=cwd)))

    def run_cli(self, *argv, code=0) -> str:
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(main(list(argv)), code)
        return out.getvalue()

    def project_json(self, *extra) -> dict:
        return json.loads(self.run_cli("tokens", "--period", "all", "--by", "project", "--json", *extra))

    def test_hide_keeps_totals_and_secret_flag_reveals(self):
        self.assertIn("Hidden: /w/secret-client", self.run_cli("hide", "secret"))
        data = self.project_json()
        self.assertEqual([g["name"] for g in data["groups"]], ["/w/public"])
        self.assertEqual(data["total"]["output"], 30)  # hidden project still counted in totals
        self.assertNotIn("secret", self.run_cli("tokens", "--period", "all", "--by", "project"))
        self.assertEqual(len(self.run_cli("tokens", "--period", "all", "--by", "model").splitlines()), 5)

        revealed = self.project_json("--show-hidden")
        self.assertEqual({g["name"]: g.get("hidden", False) for g in revealed["groups"]},
                         {"/w/public": False, "/w/secret-client": True})
        self.assertIn("/w/secret-client (hidden)",
                      self.run_cli("tokens", "--period", "all", "--by", "project", "--show-hidden"))

    def test_unhide(self):
        self.run_cli("hide", "/w/secret-client", "public")
        self.assertEqual(self.project_json()["groups"], [])
        self.assertIn("Visible again: /w/public", self.run_cli("unhide", "public"))
        self.assertIn("Unhid 1 project(s).", self.run_cli("unhide", "--all"))
        self.assertEqual(len(self.project_json()["groups"]), 2)
        self.run_cli("unhide", code=2)
        self.run_cli("hide", "nothing-like-this", code=1)

    def test_secret_parts_not_in_help(self):
        for argv in (["--help"], ["tokens", "--help"]):
            out = io.StringIO()
            with contextlib.redirect_stdout(out), self.assertRaises(SystemExit):
                main(argv)
            text = out.getvalue()
            for word in ("hide", "hidden", "SUPPRESS"):
                self.assertNotIn(word, text, argv)


if __name__ == "__main__":
    unittest.main()
