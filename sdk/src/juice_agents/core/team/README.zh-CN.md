> [English](README.md)

# Team

`TeamRegistry` 保存 Team 声明和可复用成员定义。Runner root 使用 Team 工具协调持久工作；`TeamManager` 保存当前运行的任务、收件消息、成员和完成状态。

```text
TeamRegistry manifest ──> RunnerConfig.team ──> Runner
                                               ├─ TeamManager：任务板与收件箱
                                               ├─ AsyncTaskManager：成员轮次
                                               └─ AgentManager：成员 session
```

root 创建或选择 Team、添加成员、创建任务并控制任务更新。新 Team 初始没有成员。`team_member_create` 必须且只能选择一个来源：共享 Agent 名称或 Team 专属配置。

Team root 的 `agents_list` 会列出符合成员资格的共享 Agent。Team 专属配置只允许 `description`、`instructions` 等角色字段；Team 为其提供持久生命周期与 Team 可见性。

任务的 `eligible_members` 是一组固定的当前成员；root 创建任务时会把 `"all"` 展开为成员清单。依赖完成后，符合资格的成员可以领取任务，成功领取者会记录在 `claimed_by`。成员执行失败时，任务保留 `in_progress` 并记录错误，等待 root 审核。状态变化发出 `team_update`；`team_finish` 会释放成员 session。`team_member_stop_request` 向 root 发送结构化请求；普通消息不会停止成员，由 root 决定是否结束。

`/teams [name]` 可以读取声明，不会启动 Runner。

## 开发约定

- Team 声明和可复用成员定义由 Registry 保存；本次运行的任务板、领取、消息和完成状态由 TeamManager 保存。新 Team 无自动成员；再次选择已有 Team 时，用原成员定义启动新任务板。
- 成员创建只接受一个来源。Team 本地配置只允许角色字段，并默认采用持久生命周期和 Team 可见性；共享 Agent 必须自行声明这些属性。固定协作规则放在 system prompt，用户任务只传当前动态上下文。
- Team 工具经 ToolManager 执行并验证 live Agent 身份。仅 root 可管理成员和任务、调用 `team_finish`；成员可查看和领取任务、完成自己的任务并发送消息。
- 创建任务时在锁下把 `eligible_members="all"` 展开为当前非 root 成员；领取时在同一把锁下检查资格、依赖和状态。失败任务由 root 审核，运行中的成员任务不能改派或删除。停止请求使用结构化 `member_stop_request`，普通消息不能触发停止。
