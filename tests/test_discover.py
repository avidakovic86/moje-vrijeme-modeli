"""Regression test: scheduler must discover only four main daily model cycles."""
import unittest
from unittest.mock import patch
from discover import discover


class DiscoveryRegressionTests(unittest.TestCase):
    def test_main_cycle_probe_does_not_crash(self):
        catalog = {"models": {m: {"runId": "2026010100"} for m in ("ecmwf", "gefs", "icon")}}
        with patch("discover.ready", return_value=True) as head:
            result = discover(catalog)
        self.assertTrue(result["hasCandidate"])
        self.assertEqual({x["model"] for x in result["candidates"]}, {"ecmwf", "gefs", "icon"})
        self.assertGreaterEqual(head.call_count, 3)
        for item in result["candidates"]:
            self.assertIn(item["candidateRun"][-2:], ("00", "06", "12", "18"))
            self.assertEqual(len(item["candidateRun"]), 10)

    def test_no_newer_run_does_not_request_fields(self):
        catalog = {"models": {m: {"runId": "9999123118"} for m in ("ecmwf", "gefs", "icon")}}
        with patch("discover.ready", return_value=True) as head:
            result = discover(catalog)
        self.assertFalse(result["hasCandidate"])
        self.assertEqual(result["candidates"], [])
        head.assert_not_called()


if __name__ == "__main__":
    unittest.main()
