from __future__ import annotations

import os
import unittest

from file_utils import atomic_write_bytes, backup_file, ensure_parent_dir, safe_int_1_9
from test_support import workspace_temp_dir


class FileUtilsTests(unittest.TestCase):
    def test_atomic_write_bytes_creates_parent_dir(self) -> None:
        with workspace_temp_dir("file_utils_atomic") as tmpdir:
            target = os.path.join(tmpdir, "nested", "value.bin")
            atomic_write_bytes(target, b"abc123")
            with open(target, "rb") as f:
                self.assertEqual(f.read(), b"abc123")

    def test_backup_file_copies_source(self) -> None:
        with workspace_temp_dir("file_utils_backup") as tmpdir:
            src = os.path.join(tmpdir, "part.sym")
            ensure_parent_dir(src)
            with open(src, "w", encoding="utf-8") as f:
                f.write("payload")
            backup = backup_file(src, os.path.join(tmpdir, "_bak"))
            with open(backup, "r", encoding="utf-8") as f:
                self.assertEqual(f.read(), "payload")

    def test_safe_int_1_9_clamps_and_defaults(self) -> None:
        self.assertEqual(safe_int_1_9("0"), 1)
        self.assertEqual(safe_int_1_9("12"), 9)
        self.assertEqual(safe_int_1_9("x", default=4), 4)


if __name__ == "__main__":
    unittest.main()
