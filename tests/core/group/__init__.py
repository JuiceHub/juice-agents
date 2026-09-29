"""agents.group tests package."""

from __future__ import annotations

import unittest
from pathlib import Path


def load_tests(loader: unittest.TestLoader, tests: unittest.TestSuite, pattern: str | None):
    """Allow `python -m unittest tests.core.group` to discover package tests."""

    del tests
    return loader.discover(
        start_dir=str(Path(__file__).parent),
        pattern=pattern or "test*.py",
        top_level_dir=str(Path(__file__).parents[3]),
    )
