"""Package-resource contracts for templates and builtin Skills."""

from __future__ import annotations

from importlib import resources
import json
from pathlib import Path


def _asset(relative_path: str):
    return resources.files("juice_agents").joinpath("_assets", *relative_path.split("/"))


def test_config_template_is_readable_via_importlib_resources() -> None:
    template = _asset("config.example.yaml")

    assert template.is_file()
    text = template.read_text(encoding="utf-8")
    assert "models:" in text
    assert "tools:" in text


def test_builtin_skill_tree_is_packaged_as_read_only_source_data() -> None:
    skill = _asset("skills/joke-expert/SKILL.md")
    data = _asset("skills/joke-expert/data/jokes.json")
    script = _asset("skills/joke-expert/scripts/get_random_joke.py")

    assert skill.is_file()
    assert data.is_file()
    assert script.is_file()
    assert "joke" in skill.read_text(encoding="utf-8").lower()
    assert isinstance(json.loads(data.read_text(encoding="utf-8")), list)
    assert "def " in script.read_text(encoding="utf-8")


def test_agent_module_has_no_legacy_skills_directory() -> None:
    # 内置技能只从包资源加载，避免在 Agent 模块下重新引入空的旧目录。
    agent_dir = Path(__file__).resolve().parents[2] / "sdk/src/juice_agents/core/agent"
    assert not (agent_dir / "skills").exists()


def test_real_config_and_runtime_state_are_not_package_resources() -> None:
    package_root = resources.files("juice_agents")

    assert not package_root.joinpath("core", "config", "config.yaml").is_file()
    assert not package_root.joinpath(".juice").is_dir()
    assert not package_root.joinpath(".env").is_file()
