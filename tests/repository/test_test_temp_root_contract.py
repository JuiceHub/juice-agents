"""Contracts for repository-local test temporary files."""

from __future__ import annotations

from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from tests.support.temp_paths import (
    TEST_TEMP_PARENT,
    get_test_temp_root,
    make_test_temp_dir,
)


REPO_ROOT = Path(__file__).resolve().parents[2]
EXPECTED_TEMP_PARENT = REPO_ROOT / "tests" / ".tmp" / "juice-agents-tests"


class TestTempRootContractTests(unittest.TestCase):
    def test_stdlib_tempfile_root_is_process_isolated_under_repository_test_tmp(self) -> None:
        """Temporary files stay local without sharing a cleanup target with another pytest process."""

        temp_root = get_test_temp_root().resolve()
        self.assertEqual(TEST_TEMP_PARENT.resolve(), EXPECTED_TEMP_PARENT.resolve())
        self.assertEqual(temp_root.parent, EXPECTED_TEMP_PARENT.resolve())
        self.assertTrue(temp_root.name.startswith("session-"))
        self.assertEqual(Path(tempfile.gettempdir()).resolve(), temp_root)

        with tempfile.TemporaryDirectory() as temp_dir:
            self.assertTrue(Path(temp_dir).resolve().is_relative_to(temp_root))

        helper_dir = make_test_temp_dir(prefix="contract-")
        self.assertTrue(helper_dir.resolve().is_relative_to(temp_root))

    def test_cleanup_in_one_process_preserves_another_process_session_root(self) -> None:
        """Concurrent pytest sessions cannot delete each other's active cwd."""

        worker_script = "\n".join(
            [
                "import sys",
                "from tests.support.temp_paths import cleanup_test_temp_root, get_test_temp_root",
                "root = get_test_temp_root()",
                "print(root, flush=True)",
                "sys.stdin.readline()",
                "assert root.is_dir(), root",
                "cleanup_test_temp_root()",
            ]
        )
        worker = subprocess.Popen(
            [sys.executable, "-c", worker_script],
            cwd=REPO_ROOT,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        try:
            assert worker.stdout is not None
            worker_root = Path(worker.stdout.readline().strip())
            self.assertEqual(worker_root.parent, EXPECTED_TEMP_PARENT)
            self.assertTrue(worker_root.is_dir())

            cleaner_script = "\n".join(
                [
                    "from tests.support.temp_paths import cleanup_test_temp_root, get_test_temp_root",
                    "get_test_temp_root()",
                    "cleanup_test_temp_root()",
                ]
            )
            subprocess.run(
                [sys.executable, "-c", cleaner_script],
                cwd=REPO_ROOT,
                check=True,
                capture_output=True,
                text=True,
            )
            self.assertTrue(worker_root.is_dir())

            assert worker.stdin is not None
            worker.stdin.write("\n")
            worker.stdin.flush()
            self.assertEqual(worker.wait(timeout=5), 0, worker.stderr.read())
        finally:
            if worker.poll() is None:
                worker.terminate()
                worker.wait(timeout=5)


if __name__ == "__main__":
    unittest.main()
