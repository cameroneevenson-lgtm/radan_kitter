from __future__ import annotations

import json
import os
import unittest
from unittest import mock

import automation_bridge


class AutomationBridgeTests(unittest.TestCase):
    def test_headless_kit_refresh_enabled_reads_env_flag(self) -> None:
        with mock.patch.dict(os.environ, {"RADAN_KITTER_HEADLESS_REFRESH_KITS": "1"}, clear=False):
            self.assertTrue(automation_bridge.headless_kit_refresh_enabled())
        with mock.patch.dict(os.environ, {"RADAN_KITTER_HEADLESS_REFRESH_KITS": "0"}, clear=False):
            self.assertFalse(automation_bridge.headless_kit_refresh_enabled())

    def test_refresh_document_headless_invokes_script_and_parses_json(self) -> None:
        payload = {"save_ok": True, "input_path": r"C:\Jobs\Demo.sym"}
        completed = mock.Mock(returncode=0, stdout=json.dumps(payload), stderr="")
        with mock.patch("automation_bridge.os.path.exists", return_value=True):
            with mock.patch("automation_bridge.subprocess.run", return_value=completed) as run:
                result = automation_bridge.refresh_document_headless(
                    r"C:\Jobs\Demo.sym",
                    thumbnail_path=r"C:\Jobs\thumb.png",
                    read_only=False,
                    skip_save=False,
                    python_exe="python.exe",
                )

        self.assertEqual(result, payload)
        command = run.call_args.args[0]
        self.assertEqual(command[0], "python.exe")
        self.assertIn(r"C:\Jobs\Demo.sym", command)
        self.assertIn("--thumbnail-path", command)

    def test_refresh_document_headless_raises_when_script_missing(self) -> None:
        with mock.patch("automation_bridge.os.path.exists", return_value=False):
            with self.assertRaisesRegex(RuntimeError, "refresh script not found"):
                automation_bridge.refresh_document_headless(r"C:\Jobs\Demo.sym")


if __name__ == "__main__":
    unittest.main()
