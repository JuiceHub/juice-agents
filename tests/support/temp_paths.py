"""Centralized temporary path helpers for Python tests.

All Python tests should write generated files below tests/.tmp/juice-agents-tests
so test artifacts are visible locally but never leak into production workspaces.
"""

from __future__ import annotations

import os
from pathlib import Path
import shutil
import tempfile
from uuid import uuid4


REPO_ROOT = Path(__file__).resolve().parents[2]
# Keep all artifacts under one repository-local parent, but give every Python
# process its own child directory.  Pytest sessions may overlap locally (for
# example when focused tests run beside the full suite), and a shared cwd may
# otherwise be removed by another process during its session cleanup.
TEST_TEMP_PARENT = REPO_ROOT / "tests" / ".tmp" / "juice-agents-tests"
TEST_TEMP_ROOT = TEST_TEMP_PARENT / f"session-{os.getpid()}-{uuid4().hex}"


def configure_test_temp_root() -> Path:
    """Route stdlib tempfile output to this process's isolated test root."""

    TEST_TEMP_ROOT.mkdir(parents=True, exist_ok=True)
    tempfile.tempdir = str(TEST_TEMP_ROOT)
    # Subprocesses that honor TMPDIR should inherit the same isolation root.
    os.environ["TMPDIR"] = str(TEST_TEMP_ROOT)
    return TEST_TEMP_ROOT


def get_test_temp_root() -> Path:
    """Return the configured repository-local root owned by this process."""

    return configure_test_temp_root()


def make_test_temp_dir(prefix: str = "case-") -> Path:
    """Create one test-owned temporary directory under this process's root."""

    return Path(tempfile.mkdtemp(prefix=prefix, dir=str(get_test_temp_root())))


def cleanup_test_temp_root() -> None:
    """Remove only this process's artifacts without disturbing parallel tests.

    The shared parent is removed with ``rmdir`` only after it becomes empty.
    Unlike recursive deletion, this cannot remove another still-running
    process's current working directory.
    """

    shutil.rmtree(TEST_TEMP_ROOT, ignore_errors=True)
    try:
        TEST_TEMP_PARENT.rmdir()
    except OSError:
        # A parallel session still owns a child directory, or a new one was
        # created while this process finished.  Either case is safe to leave.
        pass
