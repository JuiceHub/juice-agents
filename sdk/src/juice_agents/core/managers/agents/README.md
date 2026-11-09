# Agent runtime manager

> [简体中文](README.zh-CN.md)

`AgentManager` owns live instances, serial round scheduling, snapshots and
durable inboxes. Import it from `juice_agents.core.managers`; Agent protocols
and context trees live in `core/agent/`.

```python
from juice_agents import Runner
from juice_agents.core.managers import AgentManager

async def answer(workspace):
    async with await Runner.acreate(base_dir=workspace) as runner:
        manager: AgentManager = runner.agent_manager
        result = await runner.arun_round("Explain this project")
        return manager.round_result(runner.root_agent_id, result.round_id)
```

| File | Responsibility |
| --- | --- |
| `manager.py` | acquire/release, snapshots, candidate checkpoints and terminal commits |
| `inbox.py` | input persistence, acknowledgement, coroutine scheduling and cancellation |

`aacquire/arun/astream/apersist/arelease/aclose` are async execution APIs;
`apost` accepts durable input. Persistent rounds use the inbox scheduler;
functional calls release their instances before returning. One running Runner
owns each session. `round_result(agent_id, round_id)` reads committed outcomes
even after later rounds begin.

Context, consumed message IDs and round checkpoints commit atomically in
`session.json`. Cancelled queued input commits a cancelled result without
starting a model request. Closing scheduling waits for started work to unwind.
Model requests, usage and clients belong to ModelManager, which receives Agent
identity through explicit callbacks; Team dispatch remains in TeamManager.

Functional tests live in `tests/core/managers/agents/`; Agent protocol and
context tests stay in `tests/core/agent/`. Reuse the same execution path for
sync/async callers, and log Runner, Agent and round IDs at lifecycle boundaries.
