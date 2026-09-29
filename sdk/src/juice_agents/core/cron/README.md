# Cron / `/loop`

> [简体中文](README.zh-CN.md)

`juice_agents.core.cron` provides workspace-local scheduled prompt primitives for the CLI `/loop` feature.

## Behavior

- Tasks are stored in `.juice/scheduled_tasks.json`.
- The CLI polls `fire_due_cron_tasks` only while idle.
- Due prompts are queued into the same `stream_message` path as user input.
- The scheduler is process-local; closing the CLI stops task firing.

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
from juice_agents.core.cron import create_cron_task, fire_due_cron_tasks

create_cron_task(".", "*/5 * * * *", "check deploy", recurring=True)
fire_due_cron_tasks(".")
```

Use `cron_create`, `cron_list`, and `cron_delete` when exposing the capability to an Agent.

## Development conventions

- Data is written only under the workspace `.juice/`; `scheduled_tasks.lock` protects one read-modify-write and does not represent a long-running service.
- `fire_due_cron_tasks()` atomically marks a task as fired before returning its prompt. Recurring tasks update `last_fired_at`; one-shot tasks are deleted.
- `cron_list` is read-only. `cron_create` and `cron_delete` modify state and should be rejected in Plan Mode.
