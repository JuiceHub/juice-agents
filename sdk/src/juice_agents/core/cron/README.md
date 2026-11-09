# Cron / `/loop`

> [简体中文](README.zh-CN.md)

`juice_agents.core.cron` stores workspace-local scheduled prompts and supports both CLI `/loop` and direct SDK execution.

## Behavior

- Tasks are stored in `.juice/scheduled_tasks.json`.
- The CLI polls `fire_due_cron_tasks` once per second while idle; UI refreshes do not restart polling, and checks never overlap.
- Due prompts are queued into the same `stream_message` path as user input.
- SDK callers can poll `execute_due_cron_tasks` to execute each due prompt in its own Runner conversation.
- The caller owns polling; this module does not start a background scheduler.

## Task shape

```json
{
  "tasks": [
    {
      "id": "8hexid",
      "cron": "*/5 * * * *",
      "prompt": "check deploy",
      "created_at": 1710000000.0,
      "last_fired_at": 1710000300.0,
      "recurring": true
    }
  ]
}
```

## Public API

```python
from juice_agents.core.cron import create_cron_task, execute_due_cron_tasks

create_cron_task(".", "*/5 * * * *", "check deploy", recurring=True)
results = execute_due_cron_tasks(".", runner_config="agent")
for result in results:
    print(result["task"]["id"], result["status"], result["runner_id"])
```

`execute_due_cron_tasks` accepts `permission_mode`, `runner_config`, `runtime_config_path`, `runtime_overrides`, and an optional process-local `ask_handler`. Each Runner is closed after execution. Creation or execution failures return `status="failed"` and `error`; creation failures have `runner_id=None`. Round failures and cancellations preserve their actual `status`, partial `output`, and complete `result` from the typed execution error. Cleanup failures are logged and reported as `cleanup_error` without replacing the execution result. Failures in one task do not stop the remaining claimed tasks.

Use `fire_due_cron_tasks` instead when the caller owns prompt routing, as the CLI does. Both functions claim tasks before execution: one-shot tasks are removed and recurring tasks advance even when execution fails. Neither API automatically retries failures.

Use `cron_create`, `cron_list`, and `cron_delete` when exposing the capability to an Agent.

## Development conventions

- Data is written only under the workspace `.juice/`; `scheduled_tasks.lock` protects one read-modify-write and does not represent a long-running service.
- Polling reads without creating a lock when the task file is missing, empty, or has no due tasks. Claiming due tasks acquires the lock and rereads the file to prevent duplicate claims by concurrent callers.
- `fire_due_cron_tasks()` atomically marks a task as fired before returning its prompt. Recurring tasks update `last_fired_at`; one-shot tasks are deleted.
- `execute_due_cron_tasks()` requires an existing workspace directory and imports Runner only at execution time to keep the task store independent of Agent/tool loading.
- `cron_list` is read-only. `cron_create` and `cron_delete` modify state and should be rejected in Plan Mode.
