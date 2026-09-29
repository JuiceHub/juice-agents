> [English](README.md)

# Memory

`sdk/src/juice_agents/core/memory` 提供 workspace-local 持久记忆能力。它管理当前 workspace 下的 `.juice/memory/`，保存项目约定、用户偏好与跨会话仍有价值的事实。

## 能力边界

| 模块 | 职责 |
| --- | --- |
| `MemoryStore` | `.juice/memory/` 初始化、`MEMORY.md` 与 `topics/*.md` 读写、路径隔离、dream metadata/lock |
| `MemoryConfig` | 读 `memory.enabled`、`memory.auto_extract_enabled`、`memory.dream.*`，叠加 workspace `.juice/config.yaml` 覆盖值 |
| `memory_tools` | `memory_read`、`memory_search`、`memory_write`、`memory_forget`、`memory_status` |
| `prompt context` | 把 `MEMORY.md` 索引注入 root agent prompt |

## 运行目录

```text
.juice/memory/
  MEMORY.md
  topics/
    project-conventions.md
  .dream.lock
  .dream.last
```

`MEMORY.md` 是索引文件，topic 文件存放具体事实。`memory_search` 默认只搜索 `topics/*.md`，避免索引行重复污染搜索结果。

## 配置

默认值来自 SDK 内的 `config.example.yaml`；workspace 覆盖值写入 `.juice/config.yaml`。配置命令只修改 workspace 覆盖文件。

```yaml
memory:
  enabled: true
  auto_extract_enabled: false
  dream:
    enabled: true
    min_hours: 24
    min_sessions: 5
```

- `memory.enabled=false` 关闭 memory prompt 注入与 memory tools
- `memory.dream.*` 作为 workspace memory maintenance 的保留策略字段；当前不由 memory 模块启动后台循环
- `memory.auto_extract_enabled` 是预留字段，不会自动把每轮对话写入长期记忆

## CLI 与 RPC

```text
/memory
/memory search <query>
/memory view [path]
```

memory/dream 总开关由 `/config` 写 workspace YAML（CLI / Web 共享）。

CLI 调用 stdio gateway：`memory_status`、`memory_search`、`memory_view`、`set_memory_config`。
真实读写、路径隔离和配置写入都在后端；后台 Agent 调度属于 Runner 组合的
`AgentManager`/`AsyncTaskManager`，不由这个静态 memory 模块持有。

## 使用建议

- 只记录跨会话仍有价值的事实：项目约定、稳定偏好、模块边界、已确认决策
- 不记录密钥、临时错误、猜测、一次性上下文或未经用户确认的敏感信息
- 修改 memory 必须通过 memory tools 或 `MemoryStore`，不要绕过路径隔离直接写 `.juice/memory/`

## 开发约束

- `MemoryStore._resolve()` 是所有读写、删除操作的路径隔离边界；topic 存在 `topics/*.md`，`MEMORY.md` 只作索引及 prompt context 入口。
- 默认配置与 workspace 覆盖合并后，配置写入需立即重装配当前 Runner，使 prompt 与工具可见性同步。`memory.enabled=false` 同时关闭两者。
- Memory 模块只管理存储、配置与工具，不自行启动 daemon 或后台循环；`auto_extract_enabled` 目前只预留配置含义。
- `.dream.lock` 记录 owner、pid、created_at，读取时兼容旧纯文本 owner，并允许回收过期锁。相关测试放在 `tests/core/memory/` 及 Agent memory 工具和 prompt 测试目录。
