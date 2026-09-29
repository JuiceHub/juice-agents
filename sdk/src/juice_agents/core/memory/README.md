# Memory

> [简体中文](README.zh-CN.md)

`sdk/src/juice_agents/core/memory` provides workspace-local persistent memory. It manages `.juice/memory/` in the current workspace and stores project conventions, user preferences, and facts that remain useful across sessions.

> [Chinese](README.zh-CN.md)

## Scope

| Module | Responsibility |
| --- | --- |
| `MemoryStore` | Initialize `.juice/memory/`, read and write `MEMORY.md` and `topics/*.md`, enforce path isolation, and manage dream metadata/locks |
| `MemoryConfig` | Read `memory.enabled`, `memory.auto_extract_enabled`, and `memory.dream.*`, then merge workspace `.juice/config.yaml` overrides |
| `memory_tools` | `memory_read`, `memory_search`, `memory_write`, `memory_forget`, and `memory_status` |
| Prompt context | Inject the `MEMORY.md` index into the root Agent prompt |

## Runtime layout

```text
.juice/memory/
  MEMORY.md
  topics/
    project-conventions.md
  .dream.lock
  .dream.last
```

`MEMORY.md` is an index; topic files store concrete facts. `memory_search` searches only `topics/*.md` by default so index lines do not duplicate search results.

## Configuration

Defaults come from the SDK's `config.example.yaml`; workspace overrides are stored in `.juice/config.yaml`. Configuration commands modify only the workspace override file.

```yaml
memory:
  enabled: true
  auto_extract_enabled: false
  dream:
    enabled: true
    min_hours: 24
    min_sessions: 5
```

- `memory.enabled=false` disables memory prompt injection and memory tools.
- `memory.dream.*` stores retention-policy fields for workspace memory maintenance; this module does not start a background loop.
- `memory.auto_extract_enabled` is reserved and does not automatically write each conversation turn to long-term memory.

## CLI and RPC

```text
/memory
/memory search <query>
/memory view [path]
```

The `/config` command writes the memory/dream switches to workspace YAML shared by CLI and Web.

The CLI calls the stdio gateway methods `memory_status`, `memory_search`, `memory_view`, and `set_memory_config`. The backend owns reads, writes, path isolation, and configuration. Background Agent scheduling belongs to the Runner's `AgentManager` and `AsyncTaskManager`, not this static memory module.

## Usage guidance

- Record facts that remain useful across sessions: project conventions, stable preferences, module boundaries, and confirmed decisions.
- Do not record secrets, transient errors, guesses, one-off context, or sensitive information without user confirmation.
- Modify memory through memory tools or `MemoryStore`; do not bypass path isolation to write `.juice/memory/` directly.

## Development conventions

- `MemoryStore._resolve()` is the path-isolation boundary for every read, write, and delete. Topics live in `topics/*.md`; `MEMORY.md` is only an index and prompt-context entry point.
- After merging defaults with workspace overrides, configuration writes must immediately reassemble the current Runner so prompt and tool visibility stay in sync. `memory.enabled=false` disables both.
- This module manages storage, configuration, and tools only. It does not start a daemon or background loop; `auto_extract_enabled` is currently reserved.
- `.dream.lock` records `owner`, `pid`, and `created_at`, accepts legacy plain-text owners, and allows stale locks to be reclaimed. Put related tests in `tests/core/memory/` and the Agent memory-tool and prompt test directories.
