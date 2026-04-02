# Harness Architecture

> 文档类型：架构设计  
> 状态：2026-04-02 目标架构基线  
> 适用范围：`harness-engineering/`

这份文档是人类写的；无论何种情况，你都不能修改这个文档，并无条件按照这个文档实现。

该文档重新定义 `Harness Engineering` 的目标协作架构，并吸收 `Claude Code` 在 `COORDINATOR_MODE`、后台 worker 调度、`task-notification`、`continue/resume`、以及 verification contract 上的关键实现经验。

## 1. Harness 架构

### Harness Engineering 总体目标

设计一套 Agent “操作系统”，使多个 Agent 能围绕同一个任务持续协作、可恢复地推进复杂工作。如果遇到无法自动解决的问题，系统应当把问题清晰地呈现给人类，并把人类的自由文本决策以受控方式写回 runtime，再继续推动任务前进。

### Harness Engineering 的主要架构

1. 多 Agent 协作架构
2. Memory 管理
3. Agent 权限与安全

## 2. 多 Agent 协作架构设计

该部分一定要严格遵循以下原则：

1. 所有 Agent 都是 LLM Agent。工具和插件用于放大能力，但主工作单元仍然是 Agent。
2. `supervisor` 是唯一的 runtime 调度真相。其他 Agent 都不能直接调度其他 Agent，也不能直接决定 runtime 状态迁移。
3. 各 specialist agent 共享同一种 runtime session contract。对上层语义来说，核心不是区分 `continue` 与 `resume`，而是“继续同一个 session”应成为一等能力。
4. 除 `supervisor` 外，各 specialist agent 原则上都在 git worktree 中工作；但 `verification-agent` 虽然也可在 worktree 内运行命令，它不能修改项目树，也不能进行 git 写操作。
5. 所有关键产物都必须文件化保存；Claude Code 借鉴点是“在产物之上增加结构化事件总线”，而不是取消产物、取消目录、取消持久化。
6. 所有的设计与实现必须精简，简单化，不应该设置很多的 json 格式、具体状态等等。Agent 的回复确实应该是结构化信息，但信息必须精简，必须简单。而不应该过于复杂。

### 2.1 我们从 Claude Code 学什么

`Claude Code` 的关键不是“agent 很多”，而是把协调器与 worker 之间的运行时协议做硬：

1. coordinator 与 worker 的主沟通协议不是自由文本聊天，而是结构化 `task-notification`
2. worker 完成后，不是把上下文直接暴露给 coordinator，而是把摘要、结果、输出文件、usage 等作为事件投递回来
3. Claude Code 在实现层区分“运行中 worker 的补充消息”和“已停止 session 的恢复”，但对我们来说，这两者都可以统一抽象为“继续同一个 session”
4. teammate mailbox 是另一套 peer-to-peer 机制，不是 coordinator 与普通 worker 的主总线
5. verification 是独立 contract，不允许 verifier 一边验收一边悄悄修代码

对我们的含义是：

1. `research-agent`、`design-agent`、`execution-agent`、`verification-agent`、`decision-agent` 应当共享同一种 worker session contract
2. 各 agent 的差异主要体现为职责边界和工具 allowlist，而不是各自发明一套新的通信方式
3. `supervisor` 应只消费很薄的 runtime 通知，不应要求 agent 维护复杂语义

### 2.2 Agent 集合

当前目标架构中的 specialist roles 为：

1. `supervisor`
2. `decision-agent`
3. `research-agent`
4. `design-agent`
5. `execution-agent`
6. `verification-agent`
7. `cleanup-agent`

这里最重要的修正有三条：

1. 不再把旧的 `communication-agent` 融入 `supervisor`
2. 不再保留“`audit-agent` 可直接修简单错误”的混合职责
3. 不再把 agent 间交互理解成“不要文件化 handoff”，而是改成“文件化 artifact + 结构化事件总线”

## 3. Coordinator 与 Worker 的通信模型

### 3.1 主通信面

参考 Claude Code 的 `COORDINATOR_MODE`，我们的主通信面应为：

1. `supervisor -> worker`：结构化控制消息
2. `worker -> supervisor`：结构化任务通知
3. `human <-> runtime`：通过独立的人类沟通表面完成，不直接进入 worker 会话

`supervisor` 不应把 worker 当成聊天对象；worker 的完成、失败、中止都应作为 runtime 事件进入 `supervisor` inbox。

### 3.2 不采用的部分

Claude Code 中存在 teammate mailbox 这一套 peer-to-peer 机制，但那是 swarm/teammate 的通信层，不是 coordinator 与后台 worker 的主协议。

我们的 harness 当前是 `supervisor-centered` 架构，因此：

1. 不应把 teammate mailbox 作为主总线照搬进来
2. specialist agent 之间默认不直接互相发消息
3. 所有跨 agent 协作都通过 `supervisor` 路由

### 3.3 Worker 生命周期

这里不要写复杂。

对上层来说，session 只要几个状态就够了：

1. `running`
2. `waiting`
3. `completed`
4. `failed`
5. `killed`

动作也只要几个：

1. `spawn`
2. `continue`
3. `terminate`

其中最重要的一点是：

对上层来说，不需要区分 `continue` 和 `resume`。

统一理解成“继续同一个 session”就够了。

如果 runtime 内部需要处理“worker 还在跑”还是“worker 已停，需要恢复”，那是底层实现细节，不需要写成上层协议。

## 4. 角色与职责

### 4.1 Supervisor

Owner:

- runtime 调度真相
- round 状态真相
- session 生命周期真相
- gate 生命周期真相

Boundaries:

- 不直接承担 design、execution、verification 的主体工作
- 不直接修改 repo 代码
- 不直接解析自由文本人类回复并把它偷偷变成 planning 语义

Responsibilities:

- 调度所有 agent 的工作，决定 round 结束、下一 round 开始或整个任务结束
- 消费 worker 的结构化通知，并决定 `accept / reopen_execution / replan_design / route_to_decision`
- 管理 worktree、session、artifact、gate、brief
- 作为唯一调度者，决定把 blocker 交给 `decision-agent`

### 4.2 Decision Agent

Owner:

- blocker severity 判断
- 是否需要人类决策的语义判断
- 人类自由文本回复的解释与归一化

Boundaries:

- 不拥有 runtime 状态机
- 不直接开 gate
- 不直接写 app server 或 communication store

Responsibilities:

- 对 specialist agent 报上的 blocker、question、ambiguity 做决策分流
- 给出一个很薄的结论
- 在需要人类参与时生成人类可读的 decision brief
- 在收到人类回复后，给出下一步该怎么走

### 4.3 Research Agent

Owner:

- 外部信息检索
- 本地代码研究
- research report

Responsibilities:

- 根据 `supervisor` 指令做背景研究、资料检索、代码探索
- 输出 research artifact，供 `supervisor`、`design-agent`、`execution-agent` 消费
- 不直接改动实现代码

说明：

`research-agent` 是一个可选的 specialist role，但它仍然服从与其他 worker 相同的通信 contract，不应拥有特权消息面。

### 4.4 Design Agent

Owner:

- 当前 round 的 contract
- plan / slice / acceptance criteria

Responsibilities:

- 把目标收敛为可执行切片
- 写清边界、范围、验收条件、回退条件
- 在 contract 不足时向 `supervisor` 提问

### 4.5 Execution Agent

Owner:

- 当前 contract 的实现
- 实现证据

Responsibilities:

- 按 contract 实施工作
- 运行必要的实现侧验证
- 产出实现 artifact 与 commit 证据
- 在 blocker 出现时向 `supervisor` 报告事实，不自行决定打开人类 gate

### 4.6 Verification Agent

Owner:

- 当前 round 的 verification verdict
- 独立验证证据

Boundaries:

- 不能修改项目树
- 不能安装依赖
- 不能进行 git 写操作
- 不能把“看起来对”当作验证完成

Responsibilities:

- 独立运行 build、tests、typecheck、针对性探测
- 复现 bug、做回归检查、做至少一类 adversarial probe
- 输出 `PASS | FAIL | PARTIAL` verdict 及命令证据

说明：

这里明确借鉴 Claude Code 的 built-in `verificationAgent`。因此旧的 `audit-agent` 不再承担“简单错误直接修复”的职责。是否回到 `execution-agent` 或 `design-agent`，由 `supervisor` 根据 verification result 做状态裁决。

### 4.7 Cleanup Agent

Owner:

- runtime 卫生
- 恢复性维护

Responsibilities:

- 清理陈旧 session、无效临时产物、过期 runtime debris
- 发现重复、死路径、无主 artifact
- 对涉及 repo 代码删除、文档重写、结构收敛的事项，仍需回到正常 `design -> execution -> verification` 链路，不得越权直接改主线代码

## 5. 人类介入路径

是否需要人类参与，不由 specialist agent 自行决定。

合法路径应为：

1. specialist agent 报告 blocker 或 question
2. `supervisor` 判断是否要交给 `decision-agent`
3. 如果需要，`decision-agent` 给出一个很薄的结论
4. `supervisor` 根据结果决定：
   - 自动继续
   - 回到 design
   - 回到 execution
   - 打开 human gate
5. 如果需要 human gate，由 `supervisor` 发布 gate，由 communication surface 展示
6. 人类以自由文本回复
7. communication surface 原样保存回复
8. `supervisor` 再决定是否把这条回复交给 `decision-agent`
9. `decision-agent` 给出一个新的薄结论
10. `supervisor` 再把任务路由回 design / execution / fail / complete

这里和 Claude Code 的差异是：

1. Claude Code 倾向把用户沟通集中在 coordinator
2. 我们保留 `decision-agent`，用于承担自由文本决策解释这层语义责任
3. communication 只保留为“人类 I/O 表面与持久化”，不再作为 agent lane

### 5.1 人类主动消息

人类主动交流也是合法入口，而且不应要求系统先打开 gate。

推荐把人类主动消息原样接进 `supervisor` inbox，不要先发明很多类型。

处理规则：

1. 所有人类主动消息都先进入 `supervisor`
2. 如果这是某个 gate 的回复，`supervisor` 可以把它交给 `decision-agent`
3. 如果这不是 gate reply，`supervisor` 就把它当作新的外部输入处理
4. communication surface 负责原样持久化消息，不负责解释其语义

这样可以避免两个错误：

1. 只有 gate 打开时人类才“能说话”
2. 所有人类消息一进来就先被复杂结构化

## 6. Runtime 协议

### 6.1 Worker -> Supervisor 通知

Claude Code 借鉴点不是复杂 schema，而是非常薄的 `task-notification`。我们的 harness 也应该一样。

推荐格式：

```text
<task-notification>
session: exec-123
status: completed
summary: execution finished for phase 2
result: focused checks passed
output-file: .harness/artifacts/execution/exec-123/result.md
</task-notification>
```

关键要求：

1. 必填字段只有 `session`、`status`、`summary`
2. `result` 可以为空
3. `output-file` 可以为空
4. `status` 只能是受控枚举
5. 不要求 agent 填深层 JSON
6. 不要求 agent 写复杂语义

### 6.2 Supervisor -> Worker 控制消息

推荐格式：

```text
<continue>
session: exec-123
message: Fix the idempotency failure in the retry path and rerun focused checks.
</continue>
```

控制消息至少需要支持：

1. `spawn`
2. `continue`
3. `terminate`

说明：

上层协议只保留 `continue`。如果 runtime 内部需要区分“向运行中 session 追加消息”与“从 transcript 恢复后继续”，这属于实现细节，不再暴露为架构层单独动作。

### 6.3 Decision Agent 输出

`decision-agent` 也不应该输出复杂对象。

最简单的形式就够了：

```text
<decision>
status: continue | ask-human | stop
summary: this blocker changes behavior and needs a human choice
</decision>
```

关键要求：

1. 只告诉 `supervisor` 下一步大概怎么走
2. 不要很多字段
3. 不要很多术语
4. 不要把复杂语义再塞到另一份机器可解析 artifact 里

## 7. Runtime 存储布局

我们不再接受“无需设置一堆文件夹来回传递”的判断。

正确方向应是：

1. 保留文件化 artifact
2. 保留 session/transcript 持久化
3. 在这些持久化对象之上增加结构化事件总线

建议目录：

```text
.harness/
  events/
    supervisor-inbox.jsonl
  sessions/
    research/
    design/
    execution/
    verification/
    decision/
  inbox/
    research/
    design/
    execution/
    verification/
    decision/
  artifacts/
    research/
    design/
    execution/
    verification/
    decision/
  gates/
  briefs/
  worktrees/
  state.json
  mission.json
```

其中：

1. `events/` 是结构化总线
2. `sessions/` 保存 session metadata 与 transcript 索引
3. `inbox/` 保存待投递给 worker 的控制消息
4. `artifacts/` 保存真正的任务产物
5. `gates/` 与 `briefs/` 保存人类交互状态

## 8. 协作约束

必须成立的关系：

1. agent 不直接互相调度
2. agent 不直接向人类请求答复
3. specialist agent 报告事实与阻塞，不声明 runtime 级升级结论
4. 所有 blocker 先交给 `supervisor`
5. 是否进入 human gate 由 `decision-agent` 给出语义判断，由 `supervisor` 执行状态迁移
6. `verification-agent` 只验证，不修复
7. `supervisor` 只调度，不偷做 specialist 的主体工作

## 9. 与旧版 redesign 的关键修正

本次修正锁定以下方向：

1. `communication-agent` 不再并入 `supervisor`；改为 `decision-agent + communication surface`
2. `audit-agent` 改为 `verification-agent`，并采用非修改型 contract
3. 不再删除文件化 handoff，而是改为 `artifact + event bus`
4. “继续同一个 session”从 execution lane 细节提升为统一 runtime contract；`resume` 不再作为上层独立语义暴露

这才是更接近 Claude Code 实现细节、同时更适合本项目边界的 redesign 方向。

## 10. 监控页面

监控页面现在做的还不错，但是要注意：

1. `communications agent` 已经没了，监控页面需要修改。
2. 人类应该要非常清楚的知道每个 agent 的当前任务，以及有什么最终输出，这个需要在监控页面中展示。