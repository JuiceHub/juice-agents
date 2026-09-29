# Core tests

> [简体中文](README.zh-CN.md)

`tests/core/` organizes regression tests by SDK domain. End-to-end adapter, example, and package-build tests live in `tests/adapters/`, `tests/examples/`, and `tests/sdk/`.

| Directory | Main coverage |
| --- | --- |
| `agent/` | Agent sessions, model protocols, and built-in tools |
| `registry/` | Declaration resolution, validation, and runtime boundaries |
| `runner/` | Requests, recovery, background tasks, and multi-Agent scheduling |
| `graph/` | StateGraph flows and checkpoints |
| `team/` | Team tasks and member collaboration |

Run from the repository root:

```bash
conda activate juice-agents
python -m pytest tests/core -q
```

Tests should exercise behavior, use temporary workspaces, and clean up files. Use source or directory checks only for contracts that cannot be verified at runtime, such as layout and dependency direction; first assert that the target files were found. Do not hide regressions with `skip` or keep one-off migration tests. A directory containing new `test_*.py` files must include `__init__.py` so `unittest discover` also collects it.
