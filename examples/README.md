# Examples

> [简体中文](README.zh-CN.md)

Each example runs a built-in task with a real model when you execute its main
file without arguments. The example directory is its fixed workspace; configuration
and Runner state live in `.juice/`, and generated files stay in that workspace.

| Scenario | Run from repository root | Demonstrates |
| --- | --- | --- |
| Traceable research | `python examples/agent_research/__main__.py` | Research local sources, connect claims to evidence, and write a report |
| Capability evolution | `python examples/agent_evolution/__main__.py` | Create and call a reusable tool or specialist |
| Feedback operations | `python examples/group_operations/__main__.py` | Coordinate specialists to classify feedback and prioritize actions |
| Team delivery | `python examples/team_delivery/__main__.py` | Coordinate team tasks, implement a bounded requirement, and verify it |
| Per-step candidate selection | `python examples/agent_tot/__main__.py` | Generate three candidates, score them, and execute the winner at each step |

## Setup and run

Enter the environment and configure the example you want to run:

```bash
conda activate juice-agents
python -m pip install -e ./sdk
mkdir -p examples/agent_tot/.juice
cp sdk/src/juice_agents/core/config/defaults.yaml examples/agent_tot/.juice/config.yaml
# Edit config.yaml; put provider API keys in the repository root .env.
python examples/agent_tot/__main__.py
```

Configure `.juice/config.yaml` separately for each example; `--model <model_name>`
selects a model from that example's catalog. All examples can reuse the repository
root `.env`, without copying keys into every example. Credentials resolve in this
order: process environment, example `.env`, repository root `.env`, then
`~/.juice/.env`. The example and repository paths are fixed, so another current
directory or an IDE launch does not change credential lookup. These are real model runs and
require usable provider credentials.
Research uses local sources by default, so its built-in task needs no search API key.

All main files also support IDE execution, absolute paths, and other current
directories. For example, substitute your repository path below:

```bash
python /path/to/juice-agents/examples/agent_tot/__main__.py
```

From the repository root, the original module form still works:

```bash
python -m examples.agent_tot
```

A positional argument replaces the default task. Shared options are `--model`,
`--model-effort`, `--agent-type react|codeact`, `--permission-mode default|accept`,
and `--resume RUNNER_ID`; `--help` lists the scenario's arguments.

By default, tool approvals are answered in the terminal. For noninteractive runs,
explicitly pass `--permission-mode accept` to allow tool operations. `--model`,
`--model-effort`, and `--agent-type` override the root Agent; collaborating members
retain their own model and protocol settings.

```bash
python examples/agent_research/__main__.py "Compare the deployment options in the local project files"
python examples/team_delivery/__main__.py --resume <runner_id> "Continue the unfinished task and verify it"
```

Each run prints the workspace, task, Runner ID, steps, tool calls, and final
result. A new Runner is created unless `--resume` is provided; the Runner is
released on exit and its persisted state remains available for resumption.
Resuming retains the session's existing approval mode.

## ToT candidate selection

`agent_tot` defaults to `--strategy tot --breadth 3`. Its `GenerativeRewardModel`
(GRM), from `juice_agents.core.models.reward_models`, calls the provider SDK
directly to generate JSON scores and parses them into numbers. It defaults to
the root Agent's logical model name, creating its own SDK client. The entry point uses
`GenerativeRewardModel.from_config(name, context)`, reusing the same example
workspace catalog and credential precedence above. Construction and empty batches
do not resolve a provider.
Select another configured scoring model with `--reward-model`:

```bash
python examples/agent_tot/__main__.py
python examples/agent_tot/__main__.py "Design a caching strategy" --model <generation_model> --reward-model <scoring_model>
python examples/agent_tot/__main__.py --strategy greedy
```

```text
Same context → generate breadth candidates → validate → score → execute winner → next step
                                                  └── print scores and selected candidate
```

Only the selected candidate executes; rejected candidates remain in the persisted
tree. `--strategy greedy` or `--breadth 1` uses one generation request per step
without scoring. This is per-step selection without parallel path execution or
backtracking. Pass
`main(..., reward_model=GenerativeRewardModel(model_name="your-model", client=sdk_client))`
to use a native SDK client without configuration files for scoring; the client
must match the backend.
`main(..., reward_model=scorer)` also supports a custom scorer whose
`evaluate_batch(context=..., actions=...)` returns a dict with `scores`, `usage`,
and `usage_calls`; `scores` has one finite number per valid candidate.
Scorer objects are not persisted; the entry point configures
GRM again when resuming, and callers must reinject custom scorers.

## Development conventions

```text
Default/explicit task → scenario system instruction → Juice(workspace=example) → Runner stream
                                                                               └── workspace state and artifacts
```

- Keep dynamic task data in the user prompt; workflow, boundaries, and acceptance criteria belong in the scenario system instruction.
- Give each `__main__.py` a useful default task and retain both direct-file and module execution. Keep tasks bounded and independent of external input files unless supplied explicitly.
- Use the public `Juice(...).runners.create/resume` API and the example's own directory as the workspace; do not add `--workspace`. Reuse shared arguments, event rendering, and Runner cleanup from `examples/_runtime.py`.
- SDK `Runner.stream` does not parse gateway `$workflow` syntax. The research entry uses the existing `ManualCapability.install` to authorize `deep_research` explicitly without enabling automatic workflows.
- Use real configured models; do not add mocks, fake artifacts, or silent fallbacks. Always release the Runner with `runner.stop()` on success and errors.
- Add functional tests under `tests/examples/`, leave no generated test files behind, and update this README and the [project README](../README.md).
