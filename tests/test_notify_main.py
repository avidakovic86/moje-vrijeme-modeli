"""Offline tests: completion dispatch never leaks model data or uses broad access."""
import json
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"automation"))
from notify_main import dispatch,TARGET

BASE={"GITHUB_REPOSITORY":"avidakovic86/moje-vrijeme-modeli",
      "GITHUB_REF":"refs/heads/main","GITHUB_EVENT_NAME":"schedule"}

class MainDispatchTests(unittest.TestCase):
    def test_missing_secret_skips_without_network(self):
        with patch.dict(os.environ,BASE,clear=True),patch("notify_main.urllib.request.urlopen") as request:
            self.assertFalse(dispatch())
            request.assert_not_called()

    def test_verified_producer_can_start_private_workflow(self):
        with patch.dict(os.environ,{**BASE,"MAIN_WORKFLOW_DISPATCH_TOKEN":"UNIT_TEST_ONLY_TOKEN"},clear=True):
            with patch("notify_main.urllib.request.urlopen") as opener:
                response=MagicMock()
                response.__enter__.return_value.status=204
                opener.return_value=response
                self.assertTrue(dispatch())
                req=opener.call_args.args[0]
                self.assertEqual(req.full_url,TARGET)
                self.assertEqual(req.get_method(),"POST")
                self.assertEqual(json.loads(req.data),{"ref":"main"})
                self.assertEqual(req.get_header("Authorization"),"Bearer UNIT_TEST_ONLY_TOKEN")

    def test_untrusted_repository_or_branch_is_rejected(self):
        for change in ({"GITHUB_REPOSITORY":"other/repo"},
                       {"GITHUB_REF":"refs/heads/untrusted"},
                       {"GITHUB_EVENT_NAME":"pull_request"}):
            with patch.dict(os.environ,{**BASE,**change,"MAIN_WORKFLOW_DISPATCH_TOKEN":"UNIT_TEST_ONLY_TOKEN"},clear=True):
                with patch("notify_main.urllib.request.urlopen") as request:
                    with self.assertRaises(ValueError):dispatch()
                    request.assert_not_called()

if __name__=="__main__":unittest.main()
