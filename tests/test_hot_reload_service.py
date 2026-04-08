from __future__ import annotations

import json
import os
import unittest

from hot_reload_service import format_prompt_message, load_request, remaining_seconds, write_response
from test_support import workspace_temp_dir


class HotReloadServiceTests(unittest.TestCase):
    def test_format_prompt_message_includes_file_preview(self) -> None:
        msg = format_prompt_message(
            {
                "change_count": 4,
                "decision_timeout_sec": 10,
                "ts_epoch": 100.0,
                "files": ["a.py", "b.py", "c.py", "d.py"],
            },
            now_epoch=103.2,
        )
        self.assertIn("4 file(s) changed", msg)
        self.assertIn("7s", msg)
        self.assertIn("[a.py, b.py, c.py, ...]", msg)

    def test_remaining_seconds_handles_missing_timestamp(self) -> None:
        self.assertEqual(remaining_seconds({"decision_timeout_sec": 12}), 12)

    def test_write_response_round_trips_with_loader(self) -> None:
        with workspace_temp_dir("hot_reload") as tmpdir:
            response_path = os.path.join(tmpdir, "runtime", "hot_reload_response.json")
            request_path = os.path.join(tmpdir, "runtime", "hot_reload_request.json")
            write_response(
                response_path,
                "req-123",
                "accept",
                now_utc_iso_fn=lambda: "2026-04-08T12:00:00+00:00",
            )
            with open(response_path, "r", encoding="utf-8") as f:
                payload = json.load(f)
            self.assertEqual(payload["request_id"], "req-123")
            self.assertEqual(payload["action"], "accept")

            with open(request_path, "w", encoding="utf-8") as f:
                json.dump({"request_id": "req-999"}, f)
            self.assertEqual(load_request(request_path), {"request_id": "req-999"})


if __name__ == "__main__":
    unittest.main()
