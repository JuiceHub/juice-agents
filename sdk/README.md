# Juice Agents SDK

> [简体中文](README.zh-CN.md)

`sdk/` is the independently buildable Python SDK. The package is `juice-agents`, the import name is `juice_agents`, and Python 3.11 or newer is required. It provides Agents, Graphs, Teams, and resumable Runners. CLI and Web code lives in `frontend/`; protocol adapters live in `adapters/`; neither is part of the SDK package.

## Install

```bash
conda activate juice-agents
python -m pip install -e ./sdk
```

Run the command from the repository root. Build a local distribution with `python -m build --sdist --wheel --outdir sdk/dist sdk`, then install a wheel from `sdk/dist/`. Before using a hosted model, configure `.juice/config.yaml` in the workspace and provide its API key through the environment. The template is `src/juice_agents/_assets/config.example.yaml`.

## Use

```python
from juice_agents import Juice

juice = Juice(workspace=".")
runner = juice.runners.create(permission_mode="default", runner_config="agent")

print(runner.run("Analyze the request first"))
for event in runner.stream("Continue the implementation"):
    print(event)

runner = juice.runners.resume(runner_id=runner.runner_id)
runner.run("Continue the conversation")

# The UI owns a long-lived clock; the SDK exposes atomic due/tick primitives.
juice.cron.tick()
```

`run()` consumes `stream()` and returns the final result; both append to the current Runner session. Create a new Runner for a new conversation. A Runner handles one user request at a time. Finite background Agents, Graphs, and commands started by a request are awaited and their notifications are injected once. Team members run finite turns for tasks or messages and do not consume a thread while idle. A Team stream waits for the current member call to finish so complete `team_update` progress can be delivered. Cron does not block ordinary requests.

## Package boundary

The wheel contains `juice_agents`, type markers, and public built-in resources. It does not contain `adapters/`, examples, `.env`, real `config.yaml`, or `.juice` runtime data. Configuration, Runner state, and caches are written to the caller's workspace.

Runtime modules and their boundaries are documented in [Core](src/juice_agents/core/README.md).

## Development and release conventions

| Path | Responsibility |
| --- | --- |
| `pyproject.toml` | Package metadata, dependencies, and build configuration |
| `src/juice_agents/__init__.py`, `_client.py` | Stable exports and the `Juice` facade |
| `src/juice_agents/core/` | Agent, Graph, Runner, storage, and module documentation |
| `src/juice_agents/_assets/` | Templates and built-in Skills loaded through `importlib.resources` |

- `Runner.stream()` is the request entry point; `run()` only consumes its events. A Runner request is mutually exclusive and both methods append to the current session. Use `runners.create()` for a new conversation.
- Runner handles `$skill`, `$plugin`, `$graph`, Goal/Plan continuation, and background tasks associated with the request. Adapters must not simulate continuation with an empty message.
- Persist terminal task notifications before delivery; on recovery, replay only notifications that have not been consumed.
- The SDK must not import repository-level `adapters`. Runtime data belongs in the caller workspace's `.juice/`; resources are loaded with `importlib.resources`.

Verify from the repository root:

```bash
conda activate juice-agents
python -m pip install -e ./sdk
python -m pytest tests/sdk -q
```

Before release, verify the license, version, and source history. Build from a clean tree and inspect sdist, wheel contents, and resources. `sdk/build/`, `sdk/dist/`, and `sdk/src/*.egg-info/` are local build artifacts.
