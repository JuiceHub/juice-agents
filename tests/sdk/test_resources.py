"""Bundled defaults work independently of user configuration and runtime state."""

from __future__ import annotations

from importlib import resources
from pathlib import Path

import pytest
import yaml

from juice_agents.core.config.runtime_config import (
    DEFAULT_RUNTIME_CONFIG_PATH,
    read_runtime_config,
    write_workspace_config,
)


def test_sdk_defaults_are_readable_via_importlib_resources() -> None:
    defaults = resources.files("juice_agents.core.config").joinpath("defaults.yaml")

    assert defaults.is_file()
    assert DEFAULT_RUNTIME_CONFIG_PATH == Path(str(defaults)).resolve()
    payload = yaml.safe_load(defaults.read_text(encoding="utf-8"))
    assert payload["models"]
    assert payload["tools"]


def test_project_overrides_preserve_bundled_defaults(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Exercise real loading and persistence; never edit the SDK's base file.
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    workspace = tmp_path / "project"
    workspace.mkdir()
    original = DEFAULT_RUNTIME_CONFIG_PATH.read_bytes()
    defaults = yaml.safe_load(original)
    write_workspace_config(workspace, {"skills": {"enabled": False}})

    merged = read_runtime_config(workspace_dir=workspace)
    assert merged["skills"]["enabled"] is False
    assert merged["models"] == defaults["models"]
    assert read_runtime_config() == defaults
    assert DEFAULT_RUNTIME_CONFIG_PATH.read_bytes() == original


def test_agent_module_has_no_legacy_skills_directory() -> None:
    # 技能由调用方配置，不应在 Agent 代码目录重新引入旧的资源目录。
    agent_dir = Path(__file__).resolve().parents[2] / "sdk/src/juice_agents/core/agent"
    assert not (agent_dir / "skills").exists()


def test_real_config_and_runtime_state_are_not_package_resources() -> None:
    package_root = resources.files("juice_agents")

    assert not package_root.joinpath("core", "config", "config.yaml").is_file()
    assert not package_root.joinpath(".juice").is_dir()
    assert not package_root.joinpath(".env").is_file()
