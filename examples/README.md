# Examples

> [简体中文](README.zh-CN.md)

The four examples use a real model to demonstrate complete workflows. Each example is an independent entry point and workspace; its configuration, Runner state, and model artifacts live under that example's `.juice/` directory.

| Scenario | Command | Result |
| --- | --- | --- |
| Traceable research | `python -m examples.agent_research` | Report, sources, claims, and a Graph trace |
| Capability evolution | `python -m examples.agent_evolution` | Reusable tools or specialists that are created and called |
| Feedback operations | `python -m examples.group_operations` | Feedback classification, priority, and action suggestions |
| Team delivery | `python -m examples.team_delivery` | Team tasks/inbox, code artifacts, and verification results |

## Run an example

From the repository root, enter the environment and configure a model and provider key for the example you want to run:

```bash
conda activate juice-agents
python -m pip install -e ./sdk
mkdir -p examples/agent_research/.juice
cp sdk/src/juice_agents/_assets/config.example.yaml examples/agent_research/.juice/config.yaml
# Edit config.yaml and set the provider environment variables.
python -m examples.agent_research "Compare hosted and self-managed deployment for a customer portal"
```

Configure `.juice/config.yaml` separately for other examples. Commands commonly used are:

```bash
python -m examples.agent_evolution "Create release-note review capability for upgrade support"
python -m examples.group_operations "Review feedback and propose this week's operations plan"
python -m examples.team_delivery "Implement the requirement in TASK.md and verify it"
python -m examples.team_delivery --resume <runner_id> "Continue the unfinished task and report verification"
```

All four entry points support `--model`, `--model-effort`, `--agent-type react|codeact`, `--permission-mode default|accept`, and `--resume RUNNER_ID`. Without `--resume`, a new Runner is created. The process prints the Runner ID and progress events, then releases the Runner. The examples use a real model and have no offline mock path.

## Development conventions

```text
Dynamic business input → scenario system instruction → Juice(workspace=example) → Runner stream
                                                                            └── .juice/ state and artifacts
```

- User prompts contain only dynamic business information; workflow, boundaries, and acceptance criteria belong in the scenario system instruction.
- Each scenario enters through `__main__.py`, uses the public `Juice(...).runners.create/resume` API, and uses its own directory as the workspace. Do not add a `--workspace` option. Reuse common arguments, event rendering, and Runner lifecycle code from `examples/_runtime.py`.
- Scenarios use a real model configured in their dedicated workspace. Do not add mocks, fake artifacts, or silent fallbacks. Call `runner.stop()` on both success and error paths.
- Add behavior tests under `tests/examples/` for new or changed scenarios, and update this README and the [project README](../README.md).
