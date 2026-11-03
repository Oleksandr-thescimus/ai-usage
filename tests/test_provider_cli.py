"""ClaudeProvider + CLI end to end against a temp CLAUDE_CONFIG_DIR (network mocked)."""

import contextlib
import io
import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from ai_usage.cli import main
from ai_usage.providers.claude import ClaudeProvider
from ai_usage.providers.claude.limits import LimitsError
from tests.test_logs import assistant, line

FIXTURE = json.loads((Path(__file__).parent / "fixtures" / "oauth_usage.json").read_text())
FETCH = "ai_usage.providers.claude.fetch_usage_json"


class ProviderTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.config = Path(tmp.name) / "claude"
        (self.config / "projects" / "-w").mkdir(parents=True)
        env = mock.patch.dict(os.environ, {"CLAUDE_CONFIG_DIR": str(self.config),
                                           "XDG_CACHE_HOME": str(Path(tmp.name) / "cache")})
        env.start()
        self.addCleanup(env.stop)

    def write_creds(self, expires_in_s: float):
        (self.config / ".credentials.json").write_text(json.dumps({"claudeAiOauth": {
            "accessToken": "tok", "expiresAt": int((time.time() + expires_in_s) * 1000),
            "subscriptionType": "max"}}))

    def test_no_credentials(self):
        snap = ClaudeProvider().fetch_limits()
        self.assertEqual((snap.error_kind, snap.windows), ("no_credentials", []))

    def test_expired_token_skips_network(self):
        self.write_creds(-10)
        with mock.patch(FETCH) as fetch:
            snap = ClaudeProvider().fetch_limits()
        fetch.assert_not_called()
        self.assertEqual((snap.error_kind, snap.plan), ("expired", "max"))

    def test_success_then_network_error_uses_cache(self):
        self.write_creds(3600)
        with mock.patch(FETCH, return_value=FIXTURE):
            fresh = ClaudeProvider().fetch_limits()
        self.assertFalse(fresh.stale)
        self.assertIsNone(fresh.error)
        self.assertEqual(len(fresh.windows), 2)
        with mock.patch(FETCH, side_effect=LimitsError("network", "Network error: down")):
            stale = ClaudeProvider().fetch_limits()
        self.assertTrue(stale.stale)
        self.assertEqual(stale.error_kind, "network")
        self.assertEqual(stale.windows, fresh.windows)

    def run_cli(self, *argv) -> str:
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(main(list(argv)), 0)
        return out.getvalue()

    def test_cli_tokens_json(self):
        (self.config / "projects" / "-w" / "s.jsonl").write_text(
            line(assistant("m1", "r1", 3, cwd="/w")) + line(assistant("m1", "r1", 30, cwd="/w"))
            + line(assistant("m2", "r2", 5, cwd="/w", model="claude-sonnet-5-5"))
        )
        data = json.loads(self.run_cli("tokens", "--period", "all", "--by", "model", "--json"))
        self.assertEqual(data["total"]["output"], 35)
        self.assertEqual(data["total"]["requests"], 2)
        self.assertEqual({g["name"] for g in data["groups"]}, {"claude-opus-5-5", "claude-sonnet-5-5"})
        text = self.run_cli("tokens", "--period", "all", "--by", "project")
        self.assertIn("/w", text)

    def test_cli_status_text(self):
        self.write_creds(3600)
        with mock.patch(FETCH, return_value=FIXTURE):
            text = self.run_cli()
        self.assertIn("Claude · max plan", text)
        self.assertIn("Session (5h)", text)
        self.assertIn("Last 7 days", text)


if __name__ == "__main__":
    unittest.main()
