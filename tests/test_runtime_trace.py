from __future__ import annotations

import json
import os
import unittest
from unittest import mock

import runtime_trace as rt
from test_support import workspace_temp_dir


class RuntimeTraceTests(unittest.TestCase):
    def test_stage_writes_elapsed_record(self) -> None:
        with workspace_temp_dir("runtime_trace") as tmpdir:
            log_path = os.path.join(tmpdir, "runtime_trace.jsonl")
            with mock.patch.object(rt, "GLOBAL_RUNTIME_LOG_PATH", log_path):
                with mock.patch.dict(os.environ, {"RK_RUNTIME_TRACE": "1", "RK_STAGE_PROFILE": "1"}, clear=False):
                    with rt.stage("load_rpd_path", "bind_table", min_elapsed_ms=0, part_count=3):
                        pass

            with open(log_path, "r", encoding="utf-8") as handle:
                rows = [json.loads(line) for line in handle if line.strip()]

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["feature"], "load_rpd_path")
        self.assertEqual(rows[0]["phase"], "stage")
        self.assertEqual(rows[0]["stage"], "bind_table")
        self.assertEqual(rows[0]["part_count"], 3)
        self.assertIn("elapsed_ms", rows[0])

    def test_stage_failure_writes_error_record(self) -> None:
        with workspace_temp_dir("runtime_trace_fail") as tmpdir:
            log_path = os.path.join(tmpdir, "runtime_trace.jsonl")
            with mock.patch.object(rt, "GLOBAL_RUNTIME_LOG_PATH", log_path):
                with mock.patch.dict(os.environ, {"RK_RUNTIME_TRACE": "1", "RK_STAGE_PROFILE": "1"}, clear=False):
                    with self.assertRaisesRegex(RuntimeError, "boom"):
                        with rt.stage("load_rpd_path", "parse_rpd", min_elapsed_ms=0):
                            raise RuntimeError("boom")

            with open(log_path, "r", encoding="utf-8") as handle:
                rows = [json.loads(line) for line in handle if line.strip()]

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["phase"], "stage_error")
        self.assertEqual(rows[0]["stage"], "parse_rpd")
        self.assertEqual(rows[0]["error_type"], "RuntimeError")
        self.assertEqual(rows[0]["error"], "boom")


if __name__ == "__main__":
    unittest.main()
