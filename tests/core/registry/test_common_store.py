import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from juice_agents.core.registry.common import store


class AtomicStoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp_dir.name)

    def tearDown(self):
        self.tmp_dir.cleanup()

    def _temporary_files_for(self, target: Path) -> list[Path]:
        return list(target.parent.glob(f".{target.name}.*.tmp"))

    def test_atomic_write_uses_same_directory_fsync_and_replace(self):
        target = self.base / "nested" / "config.yaml"
        replace_calls: list[tuple[Path, Path]] = []
        real_replace = os.replace
        real_fsync = os.fsync

        def _replace(source, destination):
            replace_calls.append((Path(source), Path(destination)))
            real_replace(source, destination)

        with patch.object(store.os, "fsync", wraps=real_fsync) as fsync, patch.object(
            store.os,
            "replace",
            side_effect=_replace,
        ):
            store.atomic_write_text(target, "name: evolved\n")

        self.assertEqual(target.read_text(encoding="utf-8"), "name: evolved\n")
        self.assertEqual(len(replace_calls), 1)
        source, destination = replace_calls[0]
        self.assertEqual(source.parent, target.parent)
        self.assertEqual(destination, target)
        fsync.assert_called_once()
        self.assertEqual(self._temporary_files_for(target), [])

    def test_replace_failure_keeps_previous_content_and_cleans_temp_file(self):
        target = self.base / "config.yaml"
        target.write_text("version: old\n", encoding="utf-8")

        with patch.object(store.os, "replace", side_effect=OSError("replace failed")):
            with self.assertRaisesRegex(OSError, "replace failed"):
                store.atomic_write_text(target, "version: new\n")

        self.assertEqual(target.read_text(encoding="utf-8"), "version: old\n")
        self.assertEqual(self._temporary_files_for(target), [])

    def test_write_yaml_serializes_before_touching_target(self):
        target = self.base / "config.yaml"
        target.write_text("status: last-good\n", encoding="utf-8")

        with patch.object(store.yaml, "safe_dump", side_effect=TypeError("invalid payload")):
            with self.assertRaisesRegex(TypeError, "invalid payload"):
                store.write_yaml_config(target, {"invalid": object()})

        self.assertEqual(target.read_text(encoding="utf-8"), "status: last-good\n")
        self.assertEqual(self._temporary_files_for(target), [])

    def test_write_yaml_round_trips_unicode_payload(self):
        target = self.base / "config.yaml"
        payload = {"name": "研究员", "enabled": True}

        store.write_yaml_config(target, payload)

        self.assertEqual(store.read_yaml_config(target), payload)
        self.assertEqual(self._temporary_files_for(target), [])


if __name__ == "__main__":
    unittest.main()
