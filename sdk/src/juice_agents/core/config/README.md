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

```python
from juice_agents.core.config import ConfigurationContext

context = ConfigurationContext.from_workspace("/path/to/project")
config = context.read_merged_config()  # No credentials required
model = context.resolve_model("doubao_lite")
```

Development conventions:

- Pass the same context to registries, models and keyed tools. Reconstructed contexts must retain `overrides` and an explicit `dotenv_path`.
- `project_config_path` selects the base file; it is not the project override file. `workspace_config_path` is always `workspace/.juice/config.yaml`.
- Keep default data beside this module in `defaults.yaml` and declare it in `sdk/pyproject.toml`. An explicit missing base file raises an error instead of falling back to defaults.
- `read_workspace_config` / `write_workspace_config` read and write project overrides only. Do not persist the merged defaults or transient options.
- Config caches fingerprint every source separately and return isolated values. Credentials are read on demand, never exported to `os.environ` or logged. An explicitly empty environment variable overrides credential files.
- Model clients are reused only after resolving the current configuration and credentials. File removal or credential changes cannot silently retain a stale client.

Functional coverage lives in `tests/core/config/` and `tests/core/registry/test_model_catalog.py`.
