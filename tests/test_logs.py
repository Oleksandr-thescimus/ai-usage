import json
import tempfile
import unittest
from pathlib import Path

from ai_usage.providers.claude.logs import LogScanner, display_project, encode_project_dir, parse_line

CWD = "/work/my_app.v2"


def assistant(msg_id, req_id, output, *, model="claude-opus-5-5", cwd=CWD, ts="2026-11-03T10:00:00.000Z"):
    return {
        "type": "assistant",
        "timestamp": ts,
        "sessionId": "s1",
        "requestId": req_id,
        "cwd": cwd,
        "message": {
            "id": msg_id,
            "model": model,
            "usage": {
                "input_tokens": 2,
                "output_tokens": output,
                "cache_creation_input_tokens": 100,
                "cache_read_input_tokens": 1000,
            },
        },
    }


def line(obj) -> str:
    return json.dumps(obj) + "\n"


class EncodingTest(unittest.TestCase):
    def test_encode(self):
        self.assertEqual(encode_project_dir("/home/u/my_app.v2"), "-home-u-my-app-v2")

    def test_display(self):
        self.assertEqual(display_project("/home/u/dev/x", home="/home/u"), "~/dev/x")
        self.assertEqual(display_project("/home/user2/x", home="/home/u"), "/home/user2/x")


class ParseLineTest(unittest.TestCase):
    def test_skips_non_usage_lines(self):
        self.assertIsNone(parse_line(b'{"type":"user","message":{"content":"hi"}}\n'))
        self.assertIsNone(parse_line(b'{"type":"assistant", "usage": broken\n'))
        self.assertIsNone(parse_line(line(assistant("m", "r", 1, model="<synthetic>")).encode()))

    def test_parses(self):
        key, rec, cwd = parse_line(line(assistant("m1", "r1", 42)).encode(), "proj")
        self.assertEqual(key, ("m1", "r1"))
        self.assertEqual((rec.output_tokens, rec.cache_read_tokens, rec.project, cwd), (42, 1000, "proj", CWD))
        self.assertIsNotNone(rec.timestamp.tzinfo)

    def test_bad_numbers_become_zero(self):
        obj = assistant("m", "r", 1)
        obj["message"]["usage"].update(input_tokens="7", output_tokens=True, cache_read_input_tokens=None)
        _, rec, _ = parse_line(line(obj).encode())
        self.assertEqual((rec.input_tokens, rec.output_tokens, rec.cache_read_tokens), (0, 0, 0))


class ScannerTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "projects"
        self.project = self.root / encode_project_dir(CWD)
        self.project.mkdir(parents=True)
        self.session = self.project / "s1.jsonl"

    def tearDown(self):
        self.tmp.cleanup()

    def test_dedup_keeps_largest_output(self):
        self.session.write_text(
            line(assistant("m1", "r1", 3))
            + '{"type":"user","message":{"content":"x"}}\n'
            + line(assistant("m1", "r1", 4259))
            + line(assistant("m1", "r1", 4259))
            + "not json at all\n"
            + line(assistant("m2", "r2", 10))
        )
        records = LogScanner([self.root]).scan()
        self.assertEqual(sorted(r.output_tokens for r in records), [10, 4259])

    def test_groups_by_launch_dir_not_cwd(self):
        self.session.write_text(
            line(assistant("m1", "r1", 1, cwd=CWD + "/src/deep"))
            + line(assistant("m2", "r2", 1, cwd=CWD))
        )
        sub = self.project / "s1" / "subagents"
        sub.mkdir(parents=True)
        (sub / "agent-a.jsonl").write_text(line(assistant("m3", "r3", 1, cwd=CWD + "/node_modules")))
        records = LogScanner([self.root]).scan()
        self.assertEqual({r.project for r in records}, {CWD})
        self.assertEqual(len(records), 3)

    def test_label_falls_back_to_shortest_cwd(self):
        other = self.root / "-some-worktree"
        other.mkdir()
        (other / "s.jsonl").write_text(
            line(assistant("a", "1", 1, cwd="/tmp/wt/sub")) + line(assistant("b", "2", 1, cwd="/tmp/wt"))
        )
        records = LogScanner([self.root]).scan()
        self.assertEqual({r.project for r in records}, {"/tmp/wt"})

    def test_incremental_reads_and_partial_lines(self):
        full = line(assistant("m2", "r2", 7))
        self.session.write_text(line(assistant("m1", "r1", 5)) + full[:30])  # m2 half written
        scanner = LogScanner([self.root])
        self.assertEqual([r.output_tokens for r in scanner.scan()], [5])
        with open(self.session, "a") as fh:
            fh.write(full[30:])
            fh.write(line(assistant("m1", "r1", 50)))  # final copy of m1
        self.assertEqual(sorted(r.output_tokens for r in scanner.scan()), [7, 50])
        self.assertEqual(len(scanner.scan()), 2)  # nothing new, nothing doubled

    def test_rewritten_file_does_not_double_count(self):
        self.session.write_text(line(assistant("m1", "r1", 5)) + line(assistant("m2", "r2", 6)))
        scanner = LogScanner([self.root])
        scanner.scan()
        self.session.write_text(line(assistant("m1", "r1", 5)))  # truncated / replaced
        self.assertEqual(len(scanner.scan()), 2)

    def test_missing_root(self):
        self.assertEqual(LogScanner([Path(self.tmp.name) / "nope"]).scan(), [])


if __name__ == "__main__":
    unittest.main()
