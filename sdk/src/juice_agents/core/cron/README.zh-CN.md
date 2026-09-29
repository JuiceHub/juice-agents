> [English](README.md)

# Cron / `/loop`

`juice_agents.core.cron` 为 CLI `/loop` 功能提供 workspace 本地的定时提示词原语。

## 行为

- 任务保存在 `.juice/scheduled_tasks.json`。
- CLI 只在空闲时轮询 `fire_due_cron_tasks`。
- 到期提示词进入与用户输入相同的 `stream_message` 路径排队执行。
- 调度器属于当前进程；关闭 CLI 后就不会继续触发任务。

## 任务结构

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

## 公开 API

```python
from juice_agents.core.cron import create_cron_task, fire_due_cron_tasks

create_cron_task(".", "*/5 * * * *", "check deploy", recurring=True)
fire_due_cron_tasks(".")
```

需要向 Agent 暴露定时任务能力时，使用 `cron_create`、`cron_list` 和 `cron_delete`。

## 开发约束

- 数据只写入 workspace 的 `.juice/`。`scheduled_tasks.lock` 只保护一次读改写，不代表常驻服务。
- `fire_due_cron_tasks()` 在返回提示词前原子标记已触发任务：重复任务更新 `last_fired_at`，一次性任务则删除。
- `cron_list` 只读；`cron_create` 和 `cron_delete` 会修改数据，Plan Mode 应拒绝执行。
