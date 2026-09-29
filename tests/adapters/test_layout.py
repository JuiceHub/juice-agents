"""Guard the public module paths used by both frontend launchers."""

import importlib.util
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[2]


class AdapterLayoutTests(unittest.TestCase):
    def test_gateway_entries_are_top_level_modules(self) -> None:
        self.assertIsNotNone(importlib.util.find_spec("adapters.stdio_gateway.entry"))
        self.assertIsNotNone(importlib.util.find_spec("adapters.web_gateway.entry"))
        self.assertFalse((ROOT / "backend").exists())

if __name__ == "__main__":
    unittest.main()
