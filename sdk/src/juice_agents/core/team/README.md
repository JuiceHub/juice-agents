# Team

> [简体中文](README.zh-CN.md)

`TeamRegistry` stores Team declarations and reusable member definitions. The
Runner root coordinates durable work with Team tools; `TeamManager` stores the
current run's tasks, inbox messages, members and completion status.

```text
TeamRegistry manifest ──> RunnerConfig.team ──> Runner
                                               ├─ TeamManager: board + inbox
                                               ├─ AsyncTaskManager: member turn
                                               └─ AgentManager: member session
```

Root creates or selects a Team, adds members, creates tasks and controls task
updates. New Teams start empty. `team_member_create` accepts exactly one source:
a shared Agent name or a Team-specific configuration.
Team root's `agents_list` lists shared Agents eligible for Team membership. A
Team-specific config can contain only role fields such as `description` and
`instructions`; Team supplies persistent lifecycle and Team visibility.
A task's `eligible_members` is a fixed list of current members, expanded from `"all"`
when root creates it. Members can claim eligible work when its dependencies
are complete; the successful claimant is stored in `claimed_by`. Failed member
turns leave the task `in_progress` with an error for root review. Team state
changes emit `team_update` events; `team_finish` releases member sessions.
`team_member_stop_request` sends a structured request to root. A plain message
does not stop the member, and root decides whether to close it.

`/teams [name]` reads declarations without starting a Runner.

## Development conventions

- Registry stores Team declarations and reusable member definitions; TeamManager stores the current board, claims, messages, and completion state. New Teams have no automatic members; selecting an existing Team starts a new board from its saved member definitions.
- Member creation accepts exactly one source. Team-local configuration allows role fields only and supplies persistent lifecycle and Team visibility; shared Agents declare those properties themselves. Put fixed collaboration rules in the system prompt and pass only dynamic task context in user messages.
- Team tools go through ToolManager and verify live Agent identity. Only root manages members and tasks or calls `team_finish`; members can inspect, claim, complete their own tasks, and send messages.
- Expand `eligible_members="all"` to current non-root members under the creation lock. Check eligibility, dependencies, and state under the same lock when claiming. Root reviews failures; running member tasks cannot be reassigned or deleted. Stopping uses a structured `member_stop_request`; ordinary messages cannot stop a member.
