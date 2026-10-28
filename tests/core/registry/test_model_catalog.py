import os
import tempfile
import textwrap
import unittest
from pathlib import Path
from unittest.mock import patch


class _FakeChatModel:
    """Capture unified constructor parameters without creating SDK clients."""

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.model_name = kwargs.get("model_name", "")

    def generate(self, messages, stop_sequence=None, *, cancel_event=None):
        return {"role": "assistant", "content": self.model_name, "reasoning_content": ""}


class RuntimeModelCatalogTests(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp_dir.name)
        self.catalog_path = self.base / "config.yaml"
        self.env_path = self.base / ".env"

    def tearDown(self):
        self.tmp_dir.cleanup()

    def _write_catalog(self, body: str) -> None:
        self.catalog_path.write_text(textwrap.dedent(body).strip() + "\n", encoding="utf-8")

    def _write_env(self, body: str) -> None:
        self.env_path.write_text(textwrap.dedent(body).strip() + "\n", encoding="utf-8")

    def test_load_runtime_model_config_reads_yaml_and_env(self):
        from juice_agents.core.config.model_catalog import load_runtime_model_config

        self._write_catalog(
            """
            models:
              gpt4o_mini:
                backend: openai
                api_base: https://api.openai.com/v1
                api_key_env: OPENAI_API_KEY
                provider_model_name: gpt-4o-mini
            """
        )
        self._write_env("OPENAI_API_KEY=test-openai-key")

        config = load_runtime_model_config(
            model_name="gpt4o_mini",
            runtime_config_path=self.catalog_path,
            dotenv_path=self.env_path,
        )

        self.assertEqual(config.model_name, "gpt4o_mini")
        self.assertEqual(config.backend, "openai")
        self.assertEqual(config.api_base, "https://api.openai.com/v1")
        self.assertEqual(config.api_key_env, "OPENAI_API_KEY")
        self.assertEqual(config.provider_model_name, "gpt-4o-mini")
        self.assertEqual(config.api_key, "test-openai-key")

    def test_api_base_env_overrides_yaml_api_base(self):
        """api_base_env 指向的变量存在时覆盖 YAML api_base；缺失时回退到 YAML。"""
        from juice_agents.core.config.model_catalog import load_runtime_model_config

        self._write_catalog(
            """
            models:
              claude:
                backend: anthropic
                api_base: https://api.anthropic.com
                api_base_env: TEST_ANTHROPIC_BASE_URL
                api_key_env: TEST_ANTHROPIC_AUTH_TOKEN
                provider_model_name: claude-sonnet-4-6
            """
        )
        kwargs = dict(model_name="claude", runtime_config_path=self.catalog_path, dotenv_path=self.env_path)

        # .env 中只有密钥、没有 base url：回退到 YAML 默认值
        self._write_env("TEST_ANTHROPIC_AUTH_TOKEN=tok")
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("TEST_ANTHROPIC_BASE_URL", None)
            self.assertEqual(load_runtime_model_config(**kwargs).api_base, "https://api.anthropic.com")

        # .env 提供 base url：覆盖 YAML
        self._write_env(
            """
            TEST_ANTHROPIC_AUTH_TOKEN=tok
            TEST_ANTHROPIC_BASE_URL=https://proxy.example.com
            """
        )
        self.assertEqual(load_runtime_model_config(**kwargs).api_base, "https://proxy.example.com")

        # 进程环境变量优先于 .env
        with patch.dict(os.environ, {"TEST_ANTHROPIC_BASE_URL": "https://env.example.com"}):
            self.assertEqual(load_runtime_model_config(**kwargs).api_base, "https://env.example.com")

    def test_list_runtime_models_returns_stable_metadata(self):
        from juice_agents.core.config.model_catalog import list_runtime_models

        self._write_catalog(
            """
            models:
              claude-opus-4-7:
                backend: anthropic
                api_base: https://api.anthropic.com
                api_key_env: ANTHROPIC_AUTH_TOKEN
                provider_model_name: claude-opus-4-7
              doubao_seed:
                backend: doubao
                api_base: https://ark.example.com/api/v3
                api_key_env: DOUBAO_API_KEY
                provider_model_name: doubao-seed-1-6
              gpt4o_mini:
                backend: openai
                api_base: https://api.openai.com/v1
                api_key_env: OPENAI_API_KEY
                provider_model_name: gpt-4o-mini
            """
        )

        models = list_runtime_models(runtime_config_path=self.catalog_path)

        self.assertEqual(
            models,
            [
                {
                    "model_name": "claude-opus-4-7",
                    "backend": "anthropic",
                    "api_base": "https://api.anthropic.com",
                    "api_key_env": "ANTHROPIC_AUTH_TOKEN",
                    "provider_model_name": "claude-opus-4-7",
                    "supported_efforts": ["disabled", "low", "medium", "high", "xhigh", "max"],
                    "thinking_mode": "adaptive",
                    "supports_budget_tokens": False,
                },
                {
                    "model_name": "doubao_seed",
                    "backend": "doubao",
                    "api_base": "https://ark.example.com/api/v3",
                    "api_key_env": "DOUBAO_API_KEY",
                    "provider_model_name": "doubao-seed-1-6",
                    "supported_efforts": ["disabled", "low", "medium", "high", "auto"],
                    "thinking_mode": "none",
                    "supports_budget_tokens": False,
                },
                {
                    "model_name": "gpt4o_mini",
                    "backend": "openai",
                    "api_base": "https://api.openai.com/v1",
                    "api_key_env": "OPENAI_API_KEY",
                    "provider_model_name": "gpt-4o-mini",
                    "supported_efforts": ["disabled", "low", "medium", "high", "auto"],
                    "thinking_mode": "none",
                    "supports_budget_tokens": False,
                },
            ],
        )

    def test_list_runtime_models_infers_claude_thinking_capabilities(self):
        from juice_agents.core.config.model_catalog import list_runtime_models

        self._write_catalog(
            """
            models:
              claude-opus-5-5:
                backend: anthropic
                api_base: https://api.anthropic.com
                api_key_env: ANTHROPIC_AUTH_TOKEN
                provider_model_name: claude-opus-5-5
              claude-sonnet-5-5:
                backend: anthropic
                api_base: https://api.anthropic.com
                api_key_env: ANTHROPIC_AUTH_TOKEN
                provider_model_name: claude-sonnet-5-5
              claude-opus-4-8:
                backend: anthropic
                api_base: https://api.anthropic.com
                api_key_env: ANTHROPIC_AUTH_TOKEN
                provider_model_name: claude-opus-4-8
              claude-opus-4-7:
                backend: anthropic
                api_base: https://api.anthropic.com
                api_key_env: ANTHROPIC_AUTH_TOKEN
                provider_model_name: claude-opus-4-7
              claude-sonnet-4-6:
                backend: anthropic
                api_base: https://api.anthropic.com
                api_key_env: ANTHROPIC_AUTH_TOKEN
                provider_model_name: claude-sonnet-4-6
              claude-haiku-4-5-20251001:
                backend: anthropic
                api_base: https://api.anthropic.com
                api_key_env: ANTHROPIC_AUTH_TOKEN
                provider_model_name: claude-haiku-4-5-20251001
            """
        )

        by_name = {
            model["model_name"]: model
            for model in list_runtime_models(runtime_config_path=self.catalog_path)
        }

        # Opus 5.5 / Sonnet 5.5：完整 effort 档位（含 xhigh/max），adaptive thinking，不支持 budget_tokens
        self.assertEqual(
            by_name["claude-opus-5-5"]["supported_efforts"],
            ["disabled", "low", "medium", "high", "xhigh", "max"],
        )
        self.assertEqual(by_name["claude-opus-5-5"]["thinking_mode"], "adaptive")
        self.assertFalse(by_name["claude-opus-5-5"]["supports_budget_tokens"])

        self.assertEqual(
            by_name["claude-sonnet-5-5"]["supported_efforts"],
            ["disabled", "low", "medium", "high", "xhigh", "max"],
        )
        self.assertEqual(by_name["claude-sonnet-5-5"]["thinking_mode"], "adaptive")
        self.assertFalse(by_name["claude-sonnet-5-5"]["supports_budget_tokens"])

        # Opus 4-8 与 4-7 同样支持完整 effort 档位（含 xhigh / max）；
        # 4-8 支持 budget tokens，4-7 不支持。
        self.assertEqual(
            by_name["claude-opus-4-8"]["supported_efforts"],
            ["disabled", "low", "medium", "high", "xhigh", "max"],
        )
        self.assertEqual(by_name["claude-opus-4-8"]["thinking_mode"], "adaptive")
        self.assertFalse(by_name["claude-opus-4-8"]["supports_budget_tokens"])  # 改为不支持
        self.assertEqual(
            by_name["claude-opus-4-7"]["supported_efforts"],
            ["disabled", "low", "medium", "high", "xhigh", "max"],
        )
        self.assertFalse(by_name["claude-opus-4-7"]["supports_budget_tokens"])
        self.assertEqual(
            by_name["claude-sonnet-4-6"]["supported_efforts"],
            ["disabled", "low", "medium", "high", "max"],
        )
        self.assertNotIn("xhigh", by_name["claude-sonnet-4-6"]["supported_efforts"])
        self.assertTrue(by_name["claude-sonnet-4-6"]["supports_budget_tokens"])
        self.assertEqual(by_name["claude-haiku-4-5-20251001"]["supported_efforts"], ["disabled"])
        self.assertEqual(by_name["claude-haiku-4-5-20251001"]["thinking_mode"], "manual")
        self.assertTrue(by_name["claude-haiku-4-5-20251001"]["supports_budget_tokens"])

    def test_list_runtime_models_allows_explicit_supported_efforts_override(self):
        from juice_agents.core.config.model_catalog import list_runtime_models

        self._write_catalog(
            """
            models:
              custom_claude:
                backend: anthropic
                api_base: https://api.anthropic.com
                api_key_env: ANTHROPIC_AUTH_TOKEN
                provider_model_name: claude-custom
                supported_efforts: [low, high]
                thinking_mode: manual
                supports_budget_tokens: true
            """
        )

        models = list_runtime_models(runtime_config_path=self.catalog_path)

        self.assertEqual(models[0]["supported_efforts"], ["disabled", "low", "high"])
        self.assertEqual(models[0]["thinking_mode"], "manual")
        self.assertTrue(models[0]["supports_budget_tokens"])

    def test_validate_model_effort_rejects_unsupported_model_effort(self):
        from juice_agents.core.config.model_catalog import validate_runtime_model_effort

        self._write_catalog(
            """
            models:
              claude-sonnet-4-6:
                backend: anthropic
                api_base: https://api.anthropic.com
                api_key_env: ANTHROPIC_AUTH_TOKEN
                provider_model_name: claude-sonnet-4-6
            """
        )

        with self.assertRaisesRegex(ValueError, "不支持 model_effort=xhigh"):
            validate_runtime_model_effort(
                model_name="claude-sonnet-4-6",
                model_effort="xhigh",
                runtime_config_path=self.catalog_path,
            )

    def test_resolve_runtime_model_passes_backend_to_unified_chat_model(self):
        from juice_agents.core.config.model_catalog import resolve_runtime_model

        self._write_catalog(
            """
            models:
              doubao_seed:
                backend: doubao
                api_base: https://ark.example.com/api/v3
                api_key_env: DOUBAO_API_KEY
                provider_model_name: doubao-seed-1-6
              gpt4o_mini:
                backend: openai
                api_base: https://api.openai.com/v1
                api_key_env: OPENAI_API_KEY
                provider_model_name: gpt-4o-mini
            """
        )
        self._write_env(
            """
            DOUBAO_API_KEY=test-doubao-key
            OPENAI_API_KEY=test-openai-key
            """
        )

        with patch("juice_agents.core.config.model_catalog.ChatModel", _FakeChatModel):
            doubao_model = resolve_runtime_model(
                model_name="doubao_seed",
                runtime_config_path=self.catalog_path,
                dotenv_path=self.env_path,
            )
            openai_model = resolve_runtime_model(
                model_name="gpt4o_mini",
                runtime_config_path=self.catalog_path,
                dotenv_path=self.env_path,
            )

        self.assertIsInstance(doubao_model, _FakeChatModel)
        self.assertEqual(doubao_model.kwargs["backend"], "doubao")
        self.assertEqual(doubao_model.kwargs["model_name"], "doubao-seed-1-6")
        self.assertEqual(doubao_model.kwargs["api_key"], "test-doubao-key")
        self.assertIsInstance(openai_model, _FakeChatModel)
        self.assertEqual(openai_model.kwargs["backend"], "openai")
        self.assertEqual(openai_model.kwargs["model_name"], "gpt-4o-mini")
        self.assertEqual(openai_model.kwargs["api_key"], "test-openai-key")

    def test_resolve_runtime_model_passes_anthropic_backend_and_credentials(self):
        from juice_agents.core.config.model_catalog import resolve_runtime_model

        self._write_catalog(
            """
            models:
              claude-opus-4-7:
                backend: anthropic
                api_base: https://api.anthropic.com
                api_key_env: ANTHROPIC_AUTH_TOKEN
                provider_model_name: claude-opus-4-7
            """
        )
        self._write_env("ANTHROPIC_AUTH_TOKEN=test-anthropic-token")

        with patch("juice_agents.core.config.model_catalog.ChatModel", _FakeChatModel), \
             patch.dict("os.environ", {"ANTHROPIC_AUTH_TOKEN": ""}, clear=False):
            os.environ.pop("ANTHROPIC_AUTH_TOKEN", None)
            model = resolve_runtime_model(
                model_name="claude-opus-4-7",
                runtime_config_path=self.catalog_path,
                dotenv_path=self.env_path,
            )

        self.assertIsInstance(model, _FakeChatModel)
        self.assertEqual(model.kwargs["backend"], "anthropic")
        self.assertEqual(model.kwargs["model_name"], "claude-opus-4-7")
        self.assertEqual(model.kwargs["api_base"], "https://api.anthropic.com")
        self.assertEqual(model.kwargs["api_key"], "test-anthropic-token")

    def test_list_runtime_models_includes_claude_series(self):
        from juice_agents.core.config.model_catalog import list_runtime_models

        self._write_catalog(
            """
            models:
              claude-opus-4-7:
                backend: anthropic
                api_base: https://api.anthropic.com
                api_key_env: ANTHROPIC_AUTH_TOKEN
                provider_model_name: claude-opus-4-7
              claude-sonnet-4-6:
                backend: anthropic
                api_base: https://api.anthropic.com
                api_key_env: ANTHROPIC_AUTH_TOKEN
                provider_model_name: claude-sonnet-4-6
              claude-haiku-4-5-20251001:
                backend: anthropic
                api_base: https://api.anthropic.com
                api_key_env: ANTHROPIC_AUTH_TOKEN
                provider_model_name: claude-haiku-4-5-20251001
            """
        )

        models = list_runtime_models(runtime_config_path=self.catalog_path)

        self.assertEqual(
            [model["model_name"] for model in models],
            ["claude-haiku-4-5-20251001", "claude-opus-4-7", "claude-sonnet-4-6"],
        )
        self.assertTrue(all(model["backend"] == "anthropic" for model in models))

    def test_resolve_runtime_model_applies_effort_as_thinking_override(self):
        from juice_agents.core.config.model_catalog import resolve_runtime_model

        self._write_catalog(
            """
            models:
              doubao_seed:
                backend: doubao
                api_base: https://ark.example.com/api/v3
                api_key_env: DOUBAO_API_KEY
                provider_model_name: doubao-seed-1-6
            """
        )
        self._write_env("DOUBAO_API_KEY=test-doubao-key")

        with patch("juice_agents.core.config.model_catalog.ChatModel", _FakeChatModel):
            model = resolve_runtime_model(
                model_name="doubao_seed",
                runtime_config_path=self.catalog_path,
                dotenv_path=self.env_path,
                model_effort="high",
            )

        self.assertEqual(model.kwargs["thinking"], {"type": "enabled", "effort": "high"})
        self.assertEqual(model.kwargs["backend"], "doubao")

    def test_resolve_runtime_model_disables_thinking_for_disabled_effort(self):
        from juice_agents.core.config.model_catalog import resolve_runtime_model

        self._write_catalog(
            """
            models:
              doubao_seed:
                backend: doubao
                api_base: https://ark.example.com/api/v3
                api_key_env: DOUBAO_API_KEY
                provider_model_name: doubao-seed-1-6
            """
        )
        self._write_env("DOUBAO_API_KEY=test-doubao-key")

        with patch("juice_agents.core.config.model_catalog.ChatModel", _FakeChatModel):
            model = resolve_runtime_model(
                model_name="doubao_seed",
                runtime_config_path=self.catalog_path,
                dotenv_path=self.env_path,
                model_effort="disabled",
            )

        self.assertEqual(model.kwargs["thinking"], {"type": "disabled"})

    def test_load_runtime_model_config_rejects_missing_models_root(self):
        from juice_agents.core.config.model_catalog import load_runtime_model_config

        self.catalog_path.write_text("not_models: {}\n", encoding="utf-8")

        with self.assertRaisesRegex(ValueError, "models 顶层字段"):
            load_runtime_model_config(
                model_name="gpt4o_mini",
                runtime_config_path=self.catalog_path,
                dotenv_path=self.env_path,
            )

    def test_load_runtime_model_config_rejects_missing_required_fields(self):
        from juice_agents.core.config.model_catalog import load_runtime_model_config

        self._write_catalog(
            """
            models:
              gpt4o_mini:
                backend: openai
                api_base: https://api.openai.com/v1
                api_key_env: OPENAI_API_KEY
            """
        )

        with self.assertRaisesRegex(ValueError, "provider_model_name"):
            load_runtime_model_config(
                model_name="gpt4o_mini",
                runtime_config_path=self.catalog_path,
                dotenv_path=self.env_path,
            )

    def test_load_runtime_model_config_rejects_missing_api_key_env_value(self):
        from juice_agents.core.config.model_catalog import load_runtime_model_config

        self._write_catalog(
            """
            models:
              gpt4o_mini:
                backend: openai
                api_base: https://api.openai.com/v1
                api_key_env: OPENAI_API_KEY
                provider_model_name: gpt-4o-mini
            """
        )
        os.environ.pop("OPENAI_API_KEY", None)

        with self.assertRaisesRegex(ValueError, "OPENAI_API_KEY"):
            load_runtime_model_config(
                model_name="gpt4o_mini",
                runtime_config_path=self.catalog_path,
                dotenv_path=self.env_path,
            )

    def test_normalize_effort_accepts_xhigh_and_max(self):
        from juice_agents.core.config.model_catalog import normalize_runtime_model_effort

        self.assertEqual(normalize_runtime_model_effort("xhigh"), "xhigh")
        self.assertEqual(normalize_runtime_model_effort("max"), "max")
        self.assertEqual(normalize_runtime_model_effort("auto"), "auto")

    def test_normalize_effort_rejects_invalid_value(self):
        from juice_agents.core.config.model_catalog import normalize_runtime_model_effort

        with self.assertRaisesRegex(ValueError, "model_effort 不合法"):
            normalize_runtime_model_effort("ultra")

    def test_build_anthropic_thinking_config(self):
        from juice_agents.core.config.model_catalog import build_anthropic_thinking_config

        self.assertEqual(build_anthropic_thinking_config("disabled"), {"type": "disabled"})
        self.assertEqual(build_anthropic_thinking_config("medium"), {"type": "adaptive"})
        self.assertEqual(build_anthropic_thinking_config("xhigh"), {"type": "adaptive"})
        self.assertEqual(build_anthropic_thinking_config(None), {"type": "disabled"})

    def test_build_anthropic_effort_config(self):
        from juice_agents.core.config.model_catalog import build_anthropic_effort_config

        self.assertIsNone(build_anthropic_effort_config("disabled"))
        self.assertIsNone(build_anthropic_effort_config(None))
        self.assertEqual(build_anthropic_effort_config("medium"), {"effort": "medium"})
        self.assertEqual(build_anthropic_effort_config("xhigh"), {"effort": "xhigh"})
        self.assertEqual(build_anthropic_effort_config("max"), {"effort": "max"})
        # auto 映射为 high（Anthropic 不支持 auto）
        self.assertEqual(build_anthropic_effort_config("auto"), {"effort": "high"})

    def test_resolve_runtime_model_anthropic_uses_effort_config(self):
        from juice_agents.core.config.model_catalog import resolve_runtime_model

        self._write_catalog(
            """
            models:
              claude_opus:
                backend: anthropic
                api_base: https://api.anthropic.com
                api_key_env: ANTHROPIC_API_KEY
                provider_model_name: claude-opus-4-7
            """
        )
        self._write_env("ANTHROPIC_API_KEY=test-key")

        with patch("juice_agents.core.config.model_catalog.ChatModel", _FakeChatModel):
            model = resolve_runtime_model(
                model_name="claude_opus",
                runtime_config_path=self.catalog_path,
                dotenv_path=self.env_path,
                model_effort="xhigh",
            )

        # Anthropic 使用 adaptive thinking + output_config effort
        self.assertEqual(model.kwargs["thinking"], {"type": "adaptive"})
        self.assertEqual(model.kwargs["effort_config"], {"effort": "xhigh"})
        self.assertEqual(model.kwargs["backend"], "anthropic")

    def test_resolve_runtime_model_reuses_cache_and_isolates_effort(self):
        """同一目录和 effort 复用实例；不同 effort 必须得到各自的请求配置。"""
        from juice_agents.core.config.model_catalog import resolve_runtime_model

        self._write_catalog(
            """
            models:
              judge:
                backend: openai
                api_base: https://example.test/v1
                api_key_env: TEST_CATALOG_CACHE_API_KEY
                provider_model_name: provider-judge
            """
        )
        self._write_env("TEST_CATALOG_CACHE_API_KEY=test-key")
        options = dict(
            model_name="judge", runtime_config_path=self.catalog_path, dotenv_path=self.env_path,
        )
        with patch("juice_agents.core.config.model_catalog.ChatModel", _FakeChatModel):
            first = resolve_runtime_model(**options, model_effort="high")
            reused = resolve_runtime_model(**options, model_effort="high")
            disabled = resolve_runtime_model(**options, model_effort="disabled")

        self.assertIs(first, reused)
        self.assertIsNot(first, disabled)
        self.assertEqual(first.kwargs["backend"], "openai")
        self.assertEqual(first.kwargs["thinking"], {"type": "enabled", "effort": "high"})
        self.assertEqual(disabled.kwargs["thinking"], {"type": "disabled"})


if __name__ == "__main__":
    unittest.main()
