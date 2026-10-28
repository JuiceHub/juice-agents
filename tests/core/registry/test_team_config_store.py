"""Schema-2 Team manifests reference workspace-global Agent declarations."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from juice_agents.core.config.context import ConfigurationContext
from juice_agents.core.registry import AgentRegistry, TeamManifest, TeamRegistry
from juice_agents.core.registry.teams.store import TeamConfigStore


def _agent(name: str, **extra: object) -> dict[str, object]:
    return {
        "name": name,
        "agent_type": "react",
        "model_config_name": "default",
        "max_steps": 3,
        "tools": [],
        "lifecycle": "persistent",
        **extra,
    }


def _registries(tmp_path: Path) -> tuple[AgentRegistry, TeamRegistry]:
    context = ConfigurationContext.from_workspace(tmp_path)
    return AgentRegistry(config_context=context), TeamRegistry(config_context=context)


def test_new_team_can_start_without_members(tmp_path: Path) -> None:
    _, teams = _registries(tmp_path)
    created = teams.create(TeamManifest(team_name="empty"))

    assert created.member_names == ()
    assert teams.instantiate("empty").member_names == ()
    assert yaml.safe_load(teams.manifest_path("empty").read_text(encoding="utf-8"))["member_names"] == []


def test_shared_and_private_members_have_one_source_per_team(tmp_path: Path) -> None:
    agents, teams = _registries(tmp_path)
    agents.save_config(_agent("developer", allowed_modes=["team"]))
    teams.create_empty("review")
    teams.create_empty("audit")

    with pytest.raises(ValueError, match="必须且只能"):
        teams.add_member("review", "worker")
    with pytest.raises(ValueError, match="必须且只能"):
        teams.add_member("review", "worker", agent_name="developer", config=_agent("worker"))

    teams.add_member("review", "shared", agent_name="developer")
    teams.add_member("review", "worker", config=_agent("worker", allowed_modes=["team"], max_steps=4))
    teams.add_member("audit", "worker", config=_agent("worker", allowed_modes=["team"], max_steps=7))

    assert teams.get_manifest("review").shared_agent_name("shared") == "developer"
    assert teams.load_member_config("review", "shared").name == "developer"
    assert not teams.member_config_path("review", "shared").exists()
    assert teams.member_config_path("review", "worker").exists()
    assert teams.load_member_config("review", "worker").max_steps == 4
    assert teams.load_member_config("audit", "worker").max_steps == 7
    agents.delete_config("developer")
    assert agents.get_source("developer") == "builtin"
    assert teams.load_member_config("review", "shared").allowed_modes == ("team",)


def test_minimal_local_member_gets_team_defaults_and_invalid_fields_fail_early(tmp_path: Path) -> None:
    agents, teams = _registries(tmp_path)
    agents.save_config(_agent("researcher", allowed_modes=["team"], description="Research"))
    agents.save_config(_agent("coder", allowed_modes=["agent"]))
    agents.save_config(_agent("short_lived", lifecycle="functional"))
    teams.create_empty("jokes")

    candidates = {item["name"]: item["description"] for item in teams.list_shared_member_candidates()}
    assert set(candidates) == {"developer", "researcher"}
    assert candidates["researcher"] == "Research"
    with pytest.raises(ValueError, match="agents_list"):
        teams.add_member("jokes", "missing", agent_name="default")
    member = teams.add_member("jokes", "dad_joker", config={
        "description": "Tells dad jokes",
        "instructions": "Tell a joke to the other member.",
    })
    assert member.lifecycle == "persistent"
    assert member.allowed_modes == ("team",)
    assert teams.load_member_config("jokes", "dad_joker").instructions == "Tell a joke to the other member."

    with pytest.raises(ValueError, match="Unknown Team member config fields.*persistent"):
        teams.add_member("jokes", "wrong", config={"persistent": True})
    with pytest.raises(ValueError, match="persistent lifecycle"):
        teams.add_member("jokes", "wrong", config={"lifecycle": "functional"})
    assert teams.get_manifest("jokes").member_names == ("dad_joker",)


def test_manifest_schema_2_has_only_global_member_names() -> None:
    manifest = TeamManifest(
        team_name="review",
        description="review implementation",
        member_names=("researcher", "developer"),
    )

    assert manifest.to_dict() == {
        "schema_version": 2,
        "team_name": "review",
        "description": "review implementation",
        "member_names": ["researcher", "developer"],
    }
    with pytest.raises(ValueError, match="schema_version"):
        TeamManifest.from_dict(
            {
                "schema_version": 1,
                "team_name": "review",
                "description": "legacy",
                "member_names": ["researcher"],
            }
        )
    with pytest.raises(ValueError, match="未知字段"):
        TeamManifest.from_dict(
            {
                **manifest.to_dict(),
                "teammates": [{"name": "researcher"}],
            }
        )


def test_store_writes_only_manifest_and_never_private_member_files(tmp_path: Path) -> None:
    store = TeamConfigStore(tmp_path)
    path = store.create(
        TeamManifest(team_name="review", member_names=("researcher", "developer"))
    )

    assert path == tmp_path / ".juice" / "teams" / "review" / "manifest.yaml"
    assert yaml.safe_load(path.read_text(encoding="utf-8")) == {
        "schema_version": 2,
        "team_name": "review",
        "description": "",
        "member_names": ["researcher", "developer"],
    }
    assert not (path.parent / "agents").exists()
    assert store.get("review").member_names == ("researcher", "developer")


def test_legacy_private_team_layout_is_not_read_or_migrated(tmp_path: Path) -> None:
    store = TeamConfigStore(tmp_path)
    legacy = store.team_dir("legacy") / "agents" / "teammates" / "worker.yaml"
    legacy.parent.mkdir(parents=True)
    legacy.write_text("name: worker\n", encoding="utf-8")

    with pytest.raises(ValueError, match="拒绝自动迁移"):
        store.get("legacy")
    assert legacy.exists()
    assert not store.manifest_path("legacy").exists()


def test_registry_resolves_members_from_global_agent_registry(tmp_path: Path) -> None:
    agents, teams = _registries(tmp_path)
    agents.save_config(_agent("researcher"))
    agents.save_config(_agent("developer"))

    created = teams.create(
        TeamManifest(
            team_name="review",
            description="review implementation",
            member_names=("researcher", "developer"),
        )
    )
    resolved = teams.instantiate("review")

    assert created.member_names == ("researcher", "developer")
    assert resolved.to_dict() == created.to_dict()
    assert teams.list() == ["default", "review"]


def test_registry_rejects_missing_global_agent_before_writing_manifest(tmp_path: Path) -> None:
    _, teams = _registries(tmp_path)

    with pytest.raises(ValueError, match="不存在的全局 Agent"):
        teams.create(TeamManifest(team_name="review", member_names=("missing",)))
    assert not teams.manifest_path("review").exists()


def test_deleting_referenced_agent_is_rejected_with_team_names(tmp_path: Path) -> None:
    agents, teams = _registries(tmp_path)
    agents.save_config(_agent("custom_worker"))
    teams.create(TeamManifest(team_name="review", member_names=("custom_worker",)))

    with pytest.raises(ValueError, match=r"review"):
        agents.delete_config("custom_worker")
    assert agents.load_config("custom_worker").name == "custom_worker"


def test_deleting_team_never_deletes_shared_global_agents(tmp_path: Path) -> None:
    agents, teams = _registries(tmp_path)
    agents.save_config(_agent("researcher"))
    teams.create(TeamManifest(team_name="review", member_names=("researcher",)))

    teams.delete("review")

    assert agents.load_config("researcher").name == "researcher"
    assert not teams.manifest_path("review").exists()


def test_cold_team_list_does_not_create_workspace_files(tmp_path: Path) -> None:
    _, teams = _registries(tmp_path)

    assert [manifest.team_name for manifest in teams.list_manifests()] == ["default"]
    assert teams.get_source("default") == "builtin"
    assert not (tmp_path / ".juice").exists()


def test_stdio_team_list_is_cold_and_does_not_bootstrap_runner(tmp_path: Path) -> None:
    from adapters.stdio_gateway.handlers import RpcHandlers

    handlers = RpcHandlers()

    result = handlers.handle_list_team_configs({"base_dir": str(tmp_path)})
    assert [team["team_name"] for team in result["teams"]] == ["default"]
    assert result["teams"][0]["source"] == "builtin"
    assert result["teams"][0]["manifest_path"] is None
    assert handlers._runtime is None
    assert not (tmp_path / ".juice").exists()


@pytest.mark.parametrize("private", [False, True])
def test_default_team_first_member_creates_only_required_declarations(tmp_path: Path, private: bool) -> None:
    agents, teams = _registries(tmp_path)
    assert teams.validate("default").member_names == ()
    assert not (tmp_path / ".juice").exists()

    source = {"config": {"instructions": "Review the patch."}} if private else {"agent_name": "developer"}
    teams.add_member("default", "reviewer", **source)

    assert teams.get_source("default") == "local"
    assert teams.validate("default").member_names == ("reviewer",)
    assert teams.member_config_path("default", "reviewer").exists() == private
    assert not agents.config_dir.exists()
    assert _registries(tmp_path)[1].load_member_config("default", "reviewer").lifecycle == "persistent"
    teams.delete("default")
    assert teams.get_source("default") == "builtin"
    assert teams.get_manifest("default").member_names == ()


def test_builtin_team_and_agent_deletion_is_read_only(tmp_path: Path) -> None:
    agents, teams = _registries(tmp_path)
    with pytest.raises(ValueError, match="内置 Agent"):
        agents.delete_config("developer")
    with pytest.raises(ValueError, match="内置 Team"):
        teams.delete("default")
    assert not (tmp_path / ".juice").exists()


def test_default_team_failed_first_member_rolls_back_files(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _, teams = _registries(tmp_path)

    def deny_commit(_manifest: TeamManifest) -> None:
        raise PermissionError("manifest write denied")

    monkeypatch.setattr(teams._store, "save_manifest", deny_commit)
    with pytest.raises(PermissionError, match="denied"):
        teams.add_member("default", "reviewer", config={"instructions": "Review."})

    assert teams.get_source("default") == "builtin"
    assert teams.get_manifest("default").member_names == ()
    assert not list(tmp_path.rglob("*.yaml"))
    assert not list(tmp_path.rglob("*.tmp"))


def test_private_member_never_falls_back_to_builtin_agent(tmp_path: Path) -> None:
    _, teams = _registries(tmp_path)
    teams.add_member("default", "developer", config={"instructions": "Private developer."})
    teams.member_config_path("default", "developer").unlink()

    with pytest.raises(FileNotFoundError):
        teams.load_member_config("default", "developer")
    with pytest.raises(ValueError, match="专属成员配置"):
        teams.validate("default")


@pytest.mark.parametrize("name", ["general", "general_worker"])
def test_agent_override_delete_checks_fallback_team_eligibility(tmp_path: Path, name: str) -> None:
    agents, teams = _registries(tmp_path)
    agents.save_config(_agent(name, allowed_modes=["team"]))
    teams.create(TeamManifest(team_name="review", member_names=(name,)))

    with pytest.raises(ValueError, match="review"):
        agents.delete_config(name)
    assert agents.get_source(name) == "local"
    assert teams.validate("review").member_names == (name,)


def test_referenced_agent_edit_preserves_member_lifecycle(tmp_path: Path) -> None:
    agents, teams = _registries(tmp_path)
    teams.add_member("default", "dev", agent_name="developer")
    with pytest.raises(ValueError, match="persistent lifecycle.*default"):
        agents.save_config(agents.load_config("developer").copy_with(lifecycle="functional"))
    assert agents.get_source("developer") == "builtin"
    assert not agents.config_dir.exists()


def test_invalid_first_team_edit_leaves_no_declarations(tmp_path: Path) -> None:
    _, teams = _registries(tmp_path)
    with pytest.raises(ValueError, match="Shared Agent"):
        teams.add_member("default", "worker", agent_name="missing")
    with pytest.raises(ValueError):
        teams.add_member("default", "worker", config={"tools": ["unknown_tool"]})
    with pytest.raises(FileNotFoundError):
        teams.add_member("missing", "worker", agent_name="developer")
    assert not (tmp_path / ".juice").exists()
