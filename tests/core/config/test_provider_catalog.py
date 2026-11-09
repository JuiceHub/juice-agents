"""Functional coverage for shared providers and independent image selection."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from juice_agents.core.config import (
    DEFAULT_RUNTIME_IMAGE_MODEL_NAME,
    list_runtime_image_models,
    list_runtime_models,
    load_runtime_image_model_config,
    load_runtime_model_config,
    read_runtime_config,
    user_config_path,
    write_workspace_config,
)
from juice_agents.core.config import provider_catalog

_ARK_KEY = "JUICE_TEST_PROVIDER_ARK_KEY"
_OTHER_KEY = "JUICE_TEST_PROVIDER_OTHER_KEY"


def _write_yaml(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(payload), encoding="utf-8")


@pytest.fixture
def catalog(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[dict, dict, Path]:
    """Isolate user configuration and credentials from the developer's machine."""

    home = tmp_path / "home"
    workspace = tmp_path / "project"
    home.mkdir()
    workspace.mkdir()
    monkeypatch.setenv("HOME", str(home))
    for name in (_ARK_KEY, _OTHER_KEY, "JUICE_TEST_PROVIDER_BASE"):
        monkeypatch.delenv(name, raising=False)
    payload = {
        "providers": {
            "ark": {
                "backend": "doubao", "api_base": "https://ark.test/api/v3", "api_key_env": _ARK_KEY,
            },
            "other_ark": {
                "backend": "doubao", "api_base": "https://other.test/api/v3", "api_key_env": _OTHER_KEY,
            },
        },
        "models": {
            "chat": {"provider": "ark", "provider_model_name": "language-lite"},
            "judge": {"provider": "ark", "provider_model_name": "language-pro"},
        },
        "image_models": {
            "seedream": {"provider": "ark", "provider_model_name": "image-model"},
            "other_image": {"provider": "other_ark", "provider_model_name": "other-deployment"},
        },
        "image": {"enabled": False, "model_name": "seedream"},
    }
    base = tmp_path / "base.yaml"
    _write_yaml(base, payload)
    return payload, {"runtime_config_path": base, "workspace_dir": workspace}, workspace


def test_language_and_image_catalogs_share_connections_without_mixing_choices(catalog, monkeypatch) -> None:
    _, options, _ = catalog
    monkeypatch.setenv(_ARK_KEY, "shared-private-key")

    chat = load_runtime_model_config(model_name="chat", **options)
    judge = load_runtime_model_config(model_name="judge", **options)
    image = load_runtime_image_model_config(**options)

    assert chat.provider_model_name == "language-lite"
    assert judge.provider_model_name == "language-pro"
    assert image.provider_model_name == "image-model"
    assert {config.provider for config in (chat, judge, image)} == {"ark"}
    assert {config.api_key for config in (chat, judge, image)} == {"shared-private-key"}
    assert [model["model_name"] for model in list_runtime_models(**options)] == ["chat", "judge"]
    assert [model["model_name"] for model in list_runtime_image_models(**options)] == ["other_image", "seedream"]


def test_provider_layers_and_environment_override_apply_to_both_catalogs(catalog, monkeypatch) -> None:
    _, options, workspace = catalog
    _write_yaml(user_config_path(), {"providers": {"ark": {"api_base": "https://user.test/v3"}}})
    (user_config_path().parent / ".env").write_text(f"{_ARK_KEY}=user-key\n", encoding="utf-8")
    (workspace / ".env").write_text(f"{_ARK_KEY}=workspace-key\n", encoding="utf-8")
    write_workspace_config(workspace, {"providers": {"ark": {"api_base": "https://project.test/v3"}}})
    transient = {"providers": {"ark": {"api_base": "https://transient.test/v3"}}}
    for load, extra in (
        (load_runtime_model_config, {"model_name": "judge"}),
        (load_runtime_image_model_config, {}),
    ):
        assert load(**options, **extra).api_base == "https://project.test/v3"
        assert load(**options, **extra).api_key == "workspace-key"
        assert load(**options, **extra, runtime_overrides=transient).api_base == "https://transient.test/v3"
    monkeypatch.setenv(_ARK_KEY, "process-key")
    monkeypatch.setenv("JUICE_TEST_PROVIDER_BASE", "https://environment.test/v3")
    transient["providers"]["ark"]["api_base_env"] = "JUICE_TEST_PROVIDER_BASE"
    for load, extra in (
        (load_runtime_model_config, {"model_name": "judge"}),
        (load_runtime_image_model_config, {}),
    ):
        config = load(**options, **extra, runtime_overrides=transient)
        assert (config.api_base, config.api_key) == ("https://environment.test/v3", "process-key")
    # Resolutions must not persist merged/transient settings.
    assert yaml.safe_load((workspace / ".juice" / "config.yaml").read_text()) == {
        "providers": {"ark": {"api_base": "https://project.test/v3"}},
    }


def test_image_selection_and_credentials_are_independent_of_chat(catalog, monkeypatch) -> None:
    _, options, workspace = catalog
    monkeypatch.setenv(_ARK_KEY, "chat-key")
    monkeypatch.setenv(_OTHER_KEY, "image-key")
    write_workspace_config(workspace, {"image": {"model_name": "other_image"}})
    image = load_runtime_image_model_config(**options)
    chat = load_runtime_model_config(model_name="chat", **options)
    assert (image.provider, image.api_key) == ("other_ark", "image-key")
    assert (chat.provider, chat.api_key) == ("ark", "chat-key")
    assert read_runtime_config(**{"config_path": options["runtime_config_path"], "workspace_dir": workspace})["image"]["enabled"] is False


def test_metadata_does_not_read_credentials_and_redacts_url_secrets(catalog, monkeypatch) -> None:
    payload, options, _ = catalog
    payload["providers"]["ark"]["api_base"] = "https://private-user:private-password@ark.test/v3?token=private-token&custom=private-query#private-fragment"
    _write_yaml(options["runtime_config_path"], payload)

    def unexpected_read(**kwargs):
        pytest.fail("Model metadata must not read credential files")

    monkeypatch.setattr(provider_catalog, "read_credential_values", unexpected_read)
    entries = list_runtime_models(**options) + list_runtime_image_models(**options)
    assert all("api_key" not in entry for entry in entries)
    assert "private-" not in repr(entries)
    assert next(entry for entry in entries if entry["model_name"] == "chat")["api_base"].startswith("https://ark.test/v3?")


def test_client_config_retains_url_but_repr_and_logs_hide_secrets(catalog, monkeypatch, caplog) -> None:
    _, options, _ = catalog
    monkeypatch.setenv(_ARK_KEY, "private-api-key")
    monkeypatch.setenv("JUICE_TEST_PROVIDER_BASE", "https://private-user:private-password@ark.test/v3?key=private-query")
    overrides = {"providers": {"ark": {"api_base_env": "JUICE_TEST_PROVIDER_BASE"}}}
    with caplog.at_level("INFO"):
        chat = load_runtime_model_config(model_name="chat", **options, runtime_overrides=overrides)
        image = load_runtime_image_model_config(**options, runtime_overrides=overrides)
    assert "private-query" in chat.api_base == image.api_base
    assert "private-" not in repr(chat) + repr(image) + caplog.text


@pytest.mark.parametrize("catalog_name", ["models", "image_models"])
def test_legacy_inline_entries_fail_with_migration_guidance_without_writes(catalog, catalog_name) -> None:
    payload, options, _ = catalog
    name = "chat" if catalog_name == "models" else "seedream"
    payload[catalog_name][name] = {
        "backend": "doubao", "api_base": "https://private-user:private-pass@ark.test/v3",
        "api_key_env": _ARK_KEY, "api_key": "private-inline-key", "provider_model_name": "legacy",
    }
    path = options["runtime_config_path"]
    _write_yaml(path, payload)
    original = path.read_bytes()
    load = load_runtime_model_config if catalog_name == "models" else load_runtime_image_model_config
    with pytest.raises(ValueError, match="请迁移") as error:
        load(model_name=name, **options)
    assert "provider: <名称>" in str(error.value)
    assert "private-" not in str(error.value)
    assert path.read_bytes() == original


@pytest.mark.parametrize("override, message", [
    ({"image_models": {"seedream": {"provider": "missing"}}}, "Provider 不存在"),
    ({"providers": {"ark": {"api_base": ""}}}, "api_base"),
    ({"providers": {"ark": {"api_key_env": ""}}}, "api_key_env"),
    ({"providers": {"ark": {"backend": "unsupported"}}}, "backend 不合法"),
    ({"providers": {"ark": {"backend": "openai"}}}, "暂不支持 backend=openai"),
    ({"image_models": {"seedream": {"provider_model_name": ""}}}, "provider_model_name"),
    ({"image_models": {"seedream": None}}, "不存在模型"),
    ({"image": {"model_name": "chat"}}, "不存在模型"),
    ({"image": {"model_name": ""}}, "image.model_name 不能为空"),
])
def test_invalid_image_configuration_fails_before_credentials(catalog, override, message) -> None:
    _, options, _ = catalog
    with pytest.raises(ValueError, match=message):
        load_runtime_image_model_config(**options, runtime_overrides=override)


def test_provider_reload_uses_latest_connection_and_credentials(catalog, monkeypatch) -> None:
    _, options, workspace = catalog
    monkeypatch.setenv(_ARK_KEY, "first-key")
    first = load_runtime_image_model_config(**options)
    monkeypatch.setenv(_ARK_KEY, "second-key")
    write_workspace_config(workspace, {"providers": {"ark": {"api_base": "https://updated.test/v3"}}})
    second = load_runtime_image_model_config(**options)
    assert (first.api_base, first.api_key) == ("https://ark.test/api/v3", "first-key")
    assert (second.api_base, second.api_key) == ("https://updated.test/v3", "second-key")
    monkeypatch.setenv(_ARK_KEY, "")
    with pytest.raises(ValueError, match=_ARK_KEY):
        load_runtime_image_model_config(**options)


def test_bundled_image_default_stays_disabled_and_uses_seedream(catalog) -> None:
    _, _, workspace = catalog
    payload = read_runtime_config(workspace_dir=workspace)
    assert payload["image"] == {"enabled": False, "model_name": DEFAULT_RUNTIME_IMAGE_MODEL_NAME}
    entry = next(model for model in list_runtime_image_models(workspace_dir=workspace) if model["model_name"] == DEFAULT_RUNTIME_IMAGE_MODEL_NAME)
    assert entry["provider_model_name"] == "doubao-seedream-4-0-250828"
    assert entry["provider"] == "ark"
