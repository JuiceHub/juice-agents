"""Functional coverage for workspace-scoped configuration and credentials."""

from __future__ import annotations

import logging
import os
from pathlib import Path

import pytest
import yaml

from juice_agents.core.agent.tools.builtin.web.web_search_tools import ApiWebSearchTool
from juice_agents.core.config import ConfigurationContext
from juice_agents.core.config import model_catalog
from juice_agents.core.config.runtime_config import (
    read_runtime_config,
    read_workspace_config,
    user_config_path,
    write_workspace_config,
)


_API_KEY_ENV = "JUICE_TEST_LAYER_API_KEY"


def _write_yaml(path: Path, payload: dict) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(payload), encoding="utf-8")
    return path


@pytest.fixture
def config_files(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path, Path]:
    """Keep every HOME, project and generated file under pytest's temp dir."""
    home = tmp_path / "用户 home"
    workspace = tmp_path / "项目 workspace"
    home.mkdir()
    workspace.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv(_API_KEY_ENV, raising=False)
    base = _write_yaml(tmp_path / "base.yaml", {
        "models": {"judge": {
            "backend": "openai", "api_base": "https://base.test/v1",
            "api_key_env": _API_KEY_ENV, "provider_model_name": "base-model",
        }},
        "tools": {"tavily": {"api_key_env": _API_KEY_ENV, "timeout": 12}},
        "runner": {"model": "judge", "language": "en", "max_steps": 10},
    })
    return home, workspace, base


def test_context_and_runtime_share_all_configuration_layers(config_files: tuple[Path, Path, Path]) -> None:
    _, workspace, base = config_files
    _write_yaml(user_config_path(), {"runner": {"language": "zh", "max_steps": 20}})
    write_workspace_config(workspace, {"runner": {"max_steps": 30}})
    context = ConfigurationContext.from_workspace(workspace, project_config_path=base)

    assert context.read_merged_config() == read_runtime_config(base, workspace_dir=workspace)
    assert context.get_mapping("runner") == {"model": "judge", "language": "zh", "max_steps": 30}
    assert read_workspace_config(workspace) == {"runner": {"max_steps": 30}}


def test_cache_tracks_added_modified_deleted_and_replaced_sources(
    config_files: tuple[Path, Path, Path],
) -> None:
    _, workspace, base = config_files
    context = ConfigurationContext.from_workspace(workspace, project_config_path=base)
    assert context.get_mapping("runner")["max_steps"] == 10

    # A source with a future mtime must not mask changes in the other layers.
    os.utime(base, ns=(2_000_000_000_000_000_000, 2_000_000_000_000_000_000))
    _write_yaml(user_config_path(), {"runner": {"max_steps": 20}})
    assert context.get_mapping("runner")["max_steps"] == 20
    project = write_workspace_config(workspace, {"runner": {"max_steps": 30}})
    assert context.get_mapping("runner")["max_steps"] == 30
    _write_yaml(project, {"runner": {"max_steps": 31}})
    assert context.get_mapping("runner")["max_steps"] == 31

    original = project.stat()
    replacement = _write_yaml(project.parent / "replacement.yaml", {"runner": {"max_steps": 32}})
    os.utime(replacement, ns=(original.st_atime_ns, original.st_mtime_ns))
    replacement.replace(project)
    assert context.get_mapping("runner")["max_steps"] == 32

    project.unlink()
    assert context.get_mapping("runner")["max_steps"] == 20
    user_config_path().unlink()
    assert context.get_mapping("runner")["max_steps"] == 10
    base.unlink()
    with pytest.raises(FileNotFoundError, match="base.yaml"):
        context.read_merged_config()


def test_cache_does_not_expose_mutable_shared_configuration(config_files: tuple[Path, Path, Path]) -> None:
    _, workspace, base = config_files
    read_runtime_config(base, workspace_dir=workspace)["runner"]["max_steps"] = 999
    assert read_runtime_config(base, workspace_dir=workspace)["runner"]["max_steps"] == 10


def test_cache_keys_include_home_and_each_workspace(
    config_files: tuple[Path, Path, Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, workspace, base = config_files
    second = tmp_path / "second"
    second.mkdir()
    _write_yaml(user_config_path(), {"runner": {"language": "first-home"}})
    write_workspace_config(workspace, {"runner": {"max_steps": 30}})
    assert read_runtime_config(base, workspace_dir=workspace)["runner"]["max_steps"] == 30
    assert read_runtime_config(base, workspace_dir=second)["runner"]["max_steps"] == 10
    monkeypatch.setenv("HOME", str(tmp_path / "other-home"))
    assert read_runtime_config(base, workspace_dir=workspace)["runner"]["language"] == "en"


def test_credentials_precedence_and_deletion_without_process_mutation(
    config_files: tuple[Path, Path, Path], monkeypatch: pytest.MonkeyPatch,
) -> None:
    home, workspace, base = config_files
    (home / ".juice").mkdir()
    global_env = home / ".juice" / ".env"
    global_env.write_text(f"{_API_KEY_ENV}=global-key\n", encoding="utf-8")
    context = ConfigurationContext.from_workspace(workspace, project_config_path=base)
    assert context.resolve_env(_API_KEY_ENV) == "global-key"
    assert _API_KEY_ENV not in os.environ

    context.dotenv_path.write_text(f"{_API_KEY_ENV}=project-key\n", encoding="utf-8")
    assert context.resolve_env(_API_KEY_ENV) == "project-key"
    monkeypatch.setenv(_API_KEY_ENV, "process-key")
    assert context.resolve_env(_API_KEY_ENV) == "process-key"
    monkeypatch.setenv(_API_KEY_ENV, "")
    assert context.resolve_env(_API_KEY_ENV) == ""
    monkeypatch.delenv(_API_KEY_ENV)
    context.dotenv_path.unlink()
    assert context.resolve_env(_API_KEY_ENV) == "global-key"
    global_env.unlink()
    assert context.resolve_env(_API_KEY_ENV) == ""


def test_model_credentials_use_workspace_or_explicit_dotenv(
    config_files: tuple[Path, Path, Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, workspace, base = config_files
    (workspace / ".env").write_text(f"{_API_KEY_ENV}=project-key\n", encoding="utf-8")
    other = tmp_path / "other"
    other.mkdir()
    (other / ".env").write_text(f"{_API_KEY_ENV}=other-key\n", encoding="utf-8")
    monkeypatch.chdir(other)
    options = dict(model_name="judge", runtime_config_path=base, workspace_dir=workspace)
    assert model_catalog.load_runtime_model_config(**options).api_key == "project-key"
    assert model_catalog.load_runtime_model_config(**options, dotenv_path=other / ".env").api_key == "other-key"
    assert _API_KEY_ENV not in os.environ


class _OfflineModel:
    def __init__(self, **kwargs: object) -> None:
        self.kwargs = kwargs


def test_model_pool_refreshes_config_and_credentials_without_logging_keys(
    config_files: tuple[Path, Path, Path], monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture,
) -> None:
    _, workspace, base = config_files
    context = ConfigurationContext.from_workspace(workspace, project_config_path=base)
    context.dotenv_path.write_text(f"{_API_KEY_ENV}=private-first-key\n", encoding="utf-8")
    monkeypatch.setattr(model_catalog, "ChatModel", _OfflineModel)
    with caplog.at_level(logging.DEBUG):
        resolved = model_catalog.load_runtime_model_config(
            model_name="judge", runtime_config_path=base, workspace_dir=workspace,
        )
        assert "private-first-key" not in repr(resolved)
        first = context.resolve_model("judge")
        assert context.resolve_model("judge") is first
        context.dotenv_path.write_text(f"{_API_KEY_ENV}=private-second-key\n", encoding="utf-8")
        second = context.resolve_model("judge")
        assert second is not first
        write_workspace_config(workspace, {"models": {"judge": {"provider_model_name": "project-model"}}})
        third = context.resolve_model("judge")
        assert third.kwargs["model_name"] == "project-model"
        assert third is not second
        context.dotenv_path.unlink()
        with pytest.raises(ValueError, match=_API_KEY_ENV):
            context.resolve_model("judge")
    assert "private-first-key" not in caplog.text
    assert "private-second-key" not in caplog.text


def test_transient_overrides_reach_models_without_writing_workspace(
    config_files: tuple[Path, Path, Path], monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, workspace, base = config_files
    monkeypatch.setenv(_API_KEY_ENV, "offline-key")
    monkeypatch.setattr(model_catalog, "ChatModel", _OfflineModel)
    overrides = {"runner": {"max_steps": 99}, "models": {"judge": {"provider_model_name": "transient-model"}}}
    context = ConfigurationContext.from_workspace(workspace, project_config_path=base, overrides=overrides)
    overrides["runner"]["max_steps"] = 100
    assert context.get_mapping("runner")["max_steps"] == 99
    assert context.resolve_model("judge").kwargs["model_name"] == "transient-model"
    assert read_workspace_config(workspace) == {}
    assert not (workspace / ".juice").exists()


def test_search_uses_matching_project_configuration_and_credentials(
    config_files: tuple[Path, Path, Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    home, workspace, base = config_files
    _write_yaml(user_config_path(), {"tools": {"tavily": {"base_url": "https://global.test/search"}}})
    (home / ".juice" / ".env").write_text(f"{_API_KEY_ENV}=global-key\n", encoding="utf-8")
    (workspace / ".env").write_text(f"{_API_KEY_ENV}=project-key\n", encoding="utf-8")
    write_workspace_config(workspace, {"tools": {"tavily": {"base_url": "https://project.test/search"}}})
    first = ApiWebSearchTool(config_context=ConfigurationContext.from_workspace(workspace, project_config_path=base))
    second_workspace = tmp_path / "second"
    second_workspace.mkdir()
    second = ApiWebSearchTool(config_context=ConfigurationContext.from_workspace(second_workspace, project_config_path=base))
    monkeypatch.chdir(second_workspace)
    assert first._read_tool_config("tavily")["base_url"] == "https://project.test/search"
    assert second._read_tool_config("tavily")["base_url"] == "https://global.test/search"
    assert first._resolve_api_key("tavily") == "project-key"
    assert second._resolve_api_key("tavily") == "global-key"


def test_missing_credentials_identify_names_and_locations(config_files: tuple[Path, Path, Path]) -> None:
    home, workspace, base = config_files
    with pytest.raises(ValueError) as error:
        model_catalog.load_runtime_model_config(model_name="judge", runtime_config_path=base, workspace_dir=workspace)
    assert _API_KEY_ENV in str(error.value)
    assert str(home / ".juice" / ".env") in str(error.value)
    assert str(workspace / ".env") in str(error.value)
    # The non-secret model catalog remains usable without a credential.
    assert model_catalog.list_runtime_models(runtime_config_path=base, workspace_dir=workspace)[0]["model_name"] == "judge"
