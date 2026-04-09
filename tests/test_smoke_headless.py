from __future__ import annotations

import json
import os
import unittest
from contextlib import redirect_stdout
from io import StringIO
from unittest import mock

import smoke_headless
from test_support import workspace_temp_dir


class SmokeHeadlessTests(unittest.TestCase):
    def test_profile_mode_writes_profiler_outputs(self) -> None:
        with workspace_temp_dir("smoke_profile") as tmpdir:
            out = StringIO()
            with mock.patch.dict(os.environ, {"RK_RUNTIME_TRACE": "0"}, clear=False):
                with redirect_stdout(out):
                    rc = smoke_headless.main(
                        [
                            "--profile",
                            "--profile-dir",
                            tmpdir,
                            "--profile-limit",
                            "5",
                        ]
                    )

            payload = json.loads(out.getvalue())
            self.assertEqual(rc, 0)
            self.assertEqual(payload["profile_limit"], 5)
            self.assertEqual(payload["profile_sort"], "cumulative")
            self.assertTrue(os.path.exists(payload["profile_prof_path"]))
            self.assertTrue(os.path.exists(payload["profile_stats_path"]))


if __name__ == "__main__":
    unittest.main()
