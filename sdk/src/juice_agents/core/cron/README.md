# Cron / `/loop`

`juice_agents.core.cron` provides workspace-local scheduled prompt primitives for the CLI `/loop` feature.

## Behavior

- Tasks are stored in `.juice/scheduled_tasks.json`.
- The CLI polls `fire_due_cron_tasks` only while it is idle.
- Due prompts are queued into the same `stream_message` path as user input.
- The scheduler is process-local: closing the CLI stops firing tasks.

## Task Shape

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

Use `cron_create`, `cron_list`, and `cron_delete` when the capability should be exposed to an agent.

## 开发约束

- 数据仅写入 workspace 的 `.juice/`；`scheduled_tasks.lock` 只保护一次读改写，不代表常驻服务。
- `fire_due_cron_tasks()` 在返回提示词前原子标记已触发任务：重复任务更新 `last_fired_at`，一次性任务删除。
- `cron_list` 只读；`cron_create` 与 `cron_delete` 是修改操作，Plan Mode 应拒绝。
