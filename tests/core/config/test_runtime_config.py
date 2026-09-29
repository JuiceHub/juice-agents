"""Regression coverage for atomic workspace YAML persistence."""

from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from juice_agents.core.config.runtime_config import read_workspace_config, write_workspace_config, workspace_config_path


def test_write_workspace_config_replaces_complete_yaml() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        workspace = Path(temp_dir)
        written = write_workspace_config(workspace, {"graphs": {"enabled": False}})

        assert written == workspace_config_path(workspace)
        assert read_workspace_config(workspace) == {"graphs": {"enabled": False}}
        assert not list(written.parent.glob(f".{written.name}.*.tmp"))


def test_atomic_write_failure_preserves_previous_file_and_cleans_temp() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        workspace = Path(temp_dir)
        path = write_workspace_config(workspace, {"skills": {"enabled": True}})
        original = path.read_text(encoding="utf-8")

        with patch("juice_agents.core.config.runtime_config.os.replace", side_effect=OSError("replace failed")):
            with pytest.raises(OSError, match="replace failed"):
                write_workspace_config(workspace, {"skills": {"enabled": False}})

        assert path.read_text(encoding="utf-8") == original
        assert not list(path.parent.glob(f".{path.name}.*.tmp"))
