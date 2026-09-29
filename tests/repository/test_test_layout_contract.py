"""Contracts for the repository-wide Python test layout."""

from __future__ import annotations

from pathlib import Path
import subprocess
import unittest


REPO_ROOT = Path(__file__).resolve().parents[2]


class PythonTestLayoutContractTests(unittest.TestCase):
    def test_tracked_python_tests_live_under_root_tests(self) -> None:
        """Keep Python tests centralized under the repository root tests/ tree."""

        tracked_files = _tracked_files()
        offenders = [
            path
            for path in tracked_files
            if path.suffix == ".py" and path.name.startswith("test_") and path.parts[0] != "tests"
        ]

        self.assertEqual(offenders, [], "Python test files must live under root tests/: " + _format(offenders))

    def test_tracked_python_test_directories_live_under_root_tests(self) -> None:
        """Prevent reintroducing module-local Python test directories."""

        tracked_files = _tracked_files()
        offenders: set[Path] = set()
        for path in tracked_files:
            if path.parts[0] == "tests":
                continue
            for index, part in enumerate(path.parts):
                if part == "tests":
                    offenders.add(Path(*path.parts[: index + 1]))

        self.assertEqual(sorted(offenders), [], "Python test directories must live under root tests/: " + _format(offenders))


def _tracked_files() -> list[Path]:
    result = subprocess.run(
        ["git", "ls-files"],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=True,
    )
    return [Path(line) for line in result.stdout.splitlines() if line]


def _format(paths: list[Path]) -> str:
    return ", ".join(str(path) for path in paths)


if __name__ == "__main__":
    unittest.main()
