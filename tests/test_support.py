from __future__ import annotations

import os
import shutil
import uuid
from contextlib import contextmanager

TEST_TMP_ROOT = os.path.join(os.path.dirname(__file__), "_tmp")
FIXTURE_ROOT = os.path.join(os.path.dirname(__file__), "fixtures")
os.makedirs(TEST_TMP_ROOT, exist_ok=True)
os.makedirs(FIXTURE_ROOT, exist_ok=True)


@contextmanager
def workspace_temp_dir(prefix: str):
    path = os.path.join(TEST_TMP_ROOT, f"{prefix}_{uuid.uuid4().hex}")
    os.makedirs(path, exist_ok=False)
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)


def fixture_path(*parts: str) -> str:
    return os.path.join(FIXTURE_ROOT, *parts)
