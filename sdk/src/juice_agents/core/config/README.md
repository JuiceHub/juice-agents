# Configuration

> [简体中文](README.zh-CN.md)

All consumers share `ConfigurationContext` and `read_runtime_config`:

```text
SDK defaults / explicit runtime_config_path
  → ~/.juice/config.yaml
  → workspace/.juice/config.yaml
  → transient startup overrides

Credentials: process environment > workspace/.env > ~/.juice/.env
```

`defaults.yaml` is the immutable SDK base configuration, included in both wheel
and sdist. Override only the settings you need in user/project files; no template
copy is required. Tests load the installed package outside the repository and
verify that saving project overrides leaves the bundled defaults unchanged.
The default logical model `doubao_lite` maps to Ark model
`doubao-seed-2-1-lite-260915`; override its catalog entry when needed.
Set `browser.executable_path` in a user or workspace configuration to reuse a
local Chromium; see the [browser setup](../agent/README.md#web-search).

Connection settings live in `providers`; model entries reference a provider.
Chat (`/model`) and GRM (`/rm`) choose independently from the same `models`
catalog. Images use `image_models` and `image.model_name`; choosing an image
model with `/image_model` does not enable generation. Enable it in `/config`.

```yaml
providers:
  ark:
    backend: doubao
    api_base: https://ark.cn-beijing.volces.com/api/v3
    api_key_env: DOUBAO_API_KEY
    # api_base_env: ARK_BASE_URL
models:
  doubao_lite:
    provider: ark
    provider_model_name: doubao-seed-2-1-lite-260915
  doubao_pro:
    provider: ark
    provider_model_name: doubao-seed-2-0-pro-260215
image_models:
  seedream_4:
    provider: ark
    provider_model_name: doubao-seedream-4-0-250828
image:
  enabled: false
  model_name: seedream_4
```

`load_runtime_model_config` serves both chat and GRM. For images,
`load_runtime_image_model_config` follows `image.model_name` unless an explicit
`model_name` is supplied. Both resolve the latest provider settings and use the
same credential precedence. `list_runtime_models` / `list_runtime_image_models`
require no key and redact credentials embedded in endpoint URLs. Image providers
currently support the Ark `doubao` backend; multiple providers and deployments
can be configured independently.

To migrate old model entries, move `backend`, `api_base`, `api_base_env` and
`api_key_env` into a named provider, replace them with `provider: <name>`, and keep
`provider_model_name` and model capabilities in the model entry. Inline connection
fields, including mixed old/new entries after layered merging, raise a migration
error. User files are never rewritten automatically.

```python
from juice_agents.core.config import ConfigurationContext

context = ConfigurationContext.from_workspace("/path/to/project")
config = context.read_merged_config()  # No credentials required
model = context.resolve_model("doubao_lite")
```

Development conventions:

- Keep workspace-dependent paths out of import-time initialization. `DEFAULT_DOTENV_PATH` is relative (`Path(".env")`); resolve credentials at use time through `credential_paths` or `ConfigurationContext`. Absolute workspace/credential paths work without a valid process cwd; implicit paths require one.
- Pass the same context to registries, models and keyed tools. Reconstructed contexts must retain `overrides` and an explicit `dotenv_path`.
- `project_config_path` selects the base file; it is not the project override file. `workspace_config_path` is always `workspace/.juice/config.yaml`.
- Keep default data beside this module in `defaults.yaml` and declare it in `sdk/pyproject.toml`. An explicit missing base file raises an error instead of falling back to defaults.
- `read_workspace_config` / `write_workspace_config` read and write project overrides only. Do not persist the merged defaults or transient options.
- Config caches fingerprint every source separately and return isolated values. Credentials are read on demand, never exported to `os.environ` or logged. An explicitly empty environment variable overrides credential files.
- Model clients are reused only after resolving the current configuration and credentials. File removal or credential changes cannot silently retain a stale client.
- Keep connection settings exclusively in `providers`; preserve model capabilities in `models`. Never use the chat model as an image connection fallback or add a separate GRM catalog. GRM selection remains process-local.

Functional coverage lives in `tests/core/config/` and `tests/core/registry/test_model_catalog.py`.
