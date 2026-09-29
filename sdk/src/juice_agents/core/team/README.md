# Team

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

## 开发约束

- Team 声明和可复用成员定义由 Registry 保存；本次运行的任务板、领取、消息和完成状态由 TeamManager 保存。新 Team 无自动成员；再次选择已有 Team 会使用原成员定义启动新任务板。
- 成员创建只接受一个来源。Team 本地配置只允许角色字段，默认持久生命周期和 Team 可见性；共享 Agent 必须自行声明这些属性。固定协作规则放 system prompt，用户任务只传当前动态上下文。
- Team 工具经 ToolManager 并验证 live Agent 身份。仅 root 可管理成员和任务、调用 `team_finish`；成员可查看、领取并完成自己的任务及发消息。
- 创建任务时将 `eligible_members="all"` 在锁下展开为当前非 root 成员；领取时同锁检查资格、依赖和状态。失败任务由 root 审核，运行中的成员任务不能改派或删除。停止请求是结构化 `member_stop_request`，普通消息不能触发停止。
