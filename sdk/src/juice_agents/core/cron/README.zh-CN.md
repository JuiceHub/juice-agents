> [English](README.md)

# Cron / `/loop`

`juice_agents.core.cron` 保存 workspace 本地的定时提示词，支持 CLI `/loop` 与 SDK 直接执行。

## 行为

- 任务保存在 `.juice/scheduled_tasks.json`。
- CLI 空闲时每秒轮询一次 `fire_due_cron_tasks`；界面刷新不重启轮询，请求不会重叠。
- 到期提示词进入与用户输入相同的 `stream_message` 路径排队执行。
- SDK 调用方可以轮询 `execute_due_cron_tasks`，为每条到期提示词创建独立的 Runner 会话。
- 调用方负责轮询；本模块不会启动后台调度器。

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
from juice_agents.core.cron import create_cron_task, execute_due_cron_tasks

create_cron_task(".", "*/5 * * * *", "check deploy", recurring=True)
results = execute_due_cron_tasks(".", runner_config="agent")
for result in results:
    print(result["task"]["id"], result["status"], result["runner_id"])
```

`execute_due_cron_tasks` 接受 `permission_mode`、`runner_config`、`runtime_config_path`、`runtime_overrides` 和可选的进程内 `ask_handler`。每个 Runner 执行后都会关闭。创建或执行失败时返回 `status="failed"` 和 `error`；创建失败时 `runner_id=None`。清理失败会记录日志，并通过 `cleanup_error` 返回，不覆盖执行结果。单个任务失败不会中断其余已领取任务。

调用方需要自行路由提示词时，使用 `fire_due_cron_tasks`，CLI 采用此方式。两个函数都在执行前领取任务：即使执行失败，一次性任务也已移除，重复任务也已推进。两个 API 都不会自动重试失败任务。

需要向 Agent 暴露定时任务能力时，使用 `cron_create`、`cron_list` 和 `cron_delete`。

定时回合失败或取消时，返回真实 `status`、部分 `output` 及类型异常携带的完整 `result`；不会把错误文本当作成功输出。

## 开发约束

- 数据只写入 workspace 的 `.juice/`。`scheduled_tasks.lock` 只保护一次读改写，不代表常驻服务。
- 任务文件不存在、为空或没有到期任务时，轮询只读且不创建锁。领取到期任务时才加锁并重读文件，防止并发调用重复领取。
- `fire_due_cron_tasks()` 在返回提示词前原子标记已触发任务：重复任务更新 `last_fired_at`，一次性任务则删除。
- `execute_due_cron_tasks()` 要求 workspace 目录已存在，仅在执行时导入 Runner，避免任务存储加载时引入 Agent/工具循环依赖。
- `cron_list` 只读；`cron_create` 和 `cron_delete` 会修改数据，Plan Mode 应拒绝执行。
