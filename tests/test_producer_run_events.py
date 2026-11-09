"""Producer-run provenance regression tests; no HTTP calls."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "automation"))
from github_artifacts import GitHub, REPO_ID, WORKFLOW

class FakeGitHub(GitHub):
    def __init__(self, payload):
        self.payload = payload
    def request(self, path, method="GET"):
        return self.payload

class ProducerRunEventsTests(unittest.TestCase):
    def run_record(self, **overrides):
        record = {
            "repository": {"id": REPO_ID},
            "head_repository": {"id": REPO_ID},
            "head_branch": "main",
            "path": WORKFLOW + "@refs/heads/main",
            "event": "push",
            "status": "completed",
            "conclusion": "success",
            "head_sha": "a" * 40,
        }
        record.update(overrides)
        return FakeGitHub(record).run(123)

    def test_verified_main_push_is_accepted(self):
        self.assertEqual(self.run_record()["event"], "push")
        for event in ("schedule", "workflow_dispatch"):
            self.assertEqual(self.run_record(event=event)["event"], event)

    def test_untrusted_events_and_runs_are_rejected(self):
        invalid = [
            {"event": "pull_request"},
            {"event": "workflow_run"},
            {"head_branch": "not-main"},
            {"head_repository": {"id": -1}},
            {"repository": {"id": -1}},
            {"path": ".github/workflows/untrusted.yml@refs/heads/main"},
            {"status": "in_progress"},
            {"conclusion": "failure"},
            {"head_sha": "invalid"},
        ]
        for changes in invalid:
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.run_record(**changes)

if __name__ == "__main__":
    unittest.main()
