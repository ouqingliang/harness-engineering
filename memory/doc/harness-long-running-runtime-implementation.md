# Harness Long-Running Runtime Implementation Plan

> 文档类型：实现计划  
> 状态：当前活动基线  
> 适用范围：`harness-engineering/` 的最小长时间运行时

## 1. 目标

本计划的目标很简单：

- 让这套 agent 能一直工作
- 普通问题不要停下来问人
- 只有真正重要的问题才通过一个统一出口问人
- 重启、失败、超时之后还能继续
- 不管任务是不是在改 Harness，本轮任务都要在验证通过后才能结束

这份计划不再把 Harness 写成新的产品系统。
它只定义最小可运行的 agent runtime。

## 2. 纠偏方向

这次实现必须避免再走回这些方向：

- 先设计一大套正式协议，再去想怎么运行
- 先拆很多 runtime 对象，再去想 loop 怎么落地
- 先讨论外部系统边界，再去想 agent 怎么持续工作
- 把 Harness 写成面向人类的复杂流程系统

这次要做的是反过来：

- 先把 loop 跑起来
- 再把状态存下来
- 再把普通问题自动处理掉
- 最后才考虑额外集成

## 3. 最小运行时模型

### 3.1 核心原则

Harness 只需要三件核心能力：

- 调度
- 状态
- 问题拦截

只要这三件事成立，agent 就能长时间工作。

### 3.2 唯一入口

`main.py` 必须成为唯一正式调度入口。

它负责：

- 读取配置
- 初始化 `.harness/`
- 选择下一个 agent
- 启动 runner
- 接收 agent 输出
- 处理普通问题
- 打开真正的人工闸门
- 保存状态

它不负责：

- 代替 agent 做领域工作
- 引入复杂协议层
- 把所有状态外包给别的系统

### 3.3 角色目录

所有运行时角色都放在同一个目录下：

- `harness-engineering/agents/`

目录至少包括：

- `agents/supervisor/`
- `agents/design-agent/`
- `agents/execution-agent/`
- `agents/audit-agent/`
- `agents/cleanup-agent/`
- `agents/communication-agent/`

这样主目录只保留入口、配置、库和文档，不再把每个角色直接摊在根目录。

### 3.4 最小运行时文件

`.harness/` 里最少只需要这些文件和目录：

- `mission.json`
- `state.json`
- `handoffs/`
- `reports/`
- `questions/`
- `answers/`
- `artifacts/`
- `locks/`
- `launchers/`

如果这些文件足够，就不要再增加更重的层。

### 3.5 最小文件内容

不需要先做复杂 schema。
先把最小可运行内容固定下来。

所有文档和运行时文本文件统一按 UTF-8 读写。

`mission.json` 至少包含：

- 当前目标
- 当前状态
- 当前轮次

`state.json` 至少包含：

- 当前 active agent
- 上一次成功完成的 agent
- 重试次数
- 最近一次运行时间

`handoffs/*.json` 至少包含：

- `from`
- `to`
- `goal`
- `inputs`
- `done_when`

`reports/*.json` 至少包含：

- `agent`
- `status`
- `summary`
- `artifacts`
- `next_hint`

`questions/*.json` 至少包含：

- `agent`
- `question`
- `blocking`
- `importance`

`answers/*.json` 至少包含：

- `question_id`
- `answer`
- `source`

这已经足够支撑第一版长时间 loop。

## 4. 谁管理谁

### 4.1 管理关系

- `supervisor`
  - 管理整个 loop
  - 管理状态机迁移
  - 管理重试
  - 管理恢复
  - 管理问题拦截
  - 管理是否需要人类介入
  - 管理 `cleanup-agent` 的短周期和长周期调度
- `design-agent`
  - 只管理当前 round 的 contract
- `execution-agent`
  - 只管理当前 contract 的实现和验证
- `audit-agent`
  - 只管理当前 round 的验收 verdict
- `cleanup-agent`
  - 管理 `round-close`、`recovery`、`maintenance` 三种 cleanup 模式
- `communication-agent`
  - 只管理对人输出和对人输入的规范化

不允许的关系：

- agent 之间直接互相调度
- agent 之间直接互相要求人类答复
- agent 直接调用 `communication-agent`
- 多个 agent 同时对人发问

### 4.2 唯一人类出口

只有 `communication-agent` 可以面向人。

其他 agent 如果卡住，只能：

- 写 question
- 等 `supervisor` 处理

不能直接问人。

## 5. 谁接受谁的信息

### 5.1 `supervisor`

`supervisor` 接收所有 agent 输出。

它读取：

- handoff
- report
- question
- answer
- state

### 5.2 `design-agent`

`design-agent` 只接收：

- 当前 handoff
- 当前 mission
- 被引用的 repo 上下文
- 已有 answer

### 5.3 `execution-agent`

`execution-agent` 只接收：

- `design-agent` 之后的 handoff
- 写入范围
- 需要跑的验证
- 已有 answer

### 5.4 `audit-agent`

`audit-agent` 只接收：

- `execution-agent` 的 report
- 验证证据
- 改动结果

### 5.5 `cleanup-agent`

`cleanup-agent` 只接收：

- `cleanup_mode`
- 当前轮次的 report
- 当前 artifact 索引
- 恢复上下文或维护窗口
- 需要进一步交回 `execution-agent` 的卫生问题候选

### 5.6 `communication-agent`

`communication-agent` 只接收：

- `supervisor` 生成的 communication brief
- 人类回复

## 6. 信息如何传

主链信息不要靠隐含聊天上下文传。

主链只通过 `.harness/` 里的 runtime 文件传：

- handoff 传任务
- report 传结果
- question 传 blocker
- answer 传自动回复或人工回复
- state 传恢复点

runner 私有内容只放在：

- `launchers/`

它不能代替 handoff / report / question / answer。

## 7. 数据流

### 7.1 主工作回环

主工作回环不是固定流水线。

目标数据流如下：

1. `supervisor` 为当前 round 选择入口 agent
2. `design-agent` 写 design report 或 question
3. `supervisor` 决定是回 `design-agent`、进入 `execution-agent`、还是先处理 blocker
4. `execution-agent` 写 execution report、verification evidence、或 question
5. `supervisor` 把当前结果交给 `audit-agent`
6. `audit-agent` 写 `accepted`、`reopen_execution`、`replan_design`、或 question
7. `supervisor` 根据 verdict 决定：
   - 回到 `execution-agent`
   - 回到 `design-agent`
   - 结束当前 round

`communication-agent` 和 `cleanup-agent` 不在这条固定主链里。

### 7.2 普通问题

普通问题的数据流如下：

1. agent 写 `questions/*.json`
2. `supervisor` 读取它
3. `supervisor` 先查当前 handoff、repo、已有 answer、默认策略
4. 如果能自动处理，就写 `answers/*.json`
5. 原 agent 继续

这条链路不经过人类。

### 7.3 重要问题

重要问题的数据流如下：

1. agent 写 `questions/*.json`
2. `supervisor` 判断这不是普通 blocker
3. `supervisor` 把它转成给 `communication-agent` 的 communication brief
4. `communication-agent` 面向人提问，并把相关 agent 观点和推荐说清楚
5. 人类回复
6. `communication-agent` 把回复写入 `answers/*.json`
7. `supervisor` 决定回复应该回到哪个 agent 或 mission
8. 原 agent 继续

这条链路是唯一允许进入人工回路的路径。

### 7.4 Cleanup 通道

`cleanup-agent` 的数据流如下：

1. `supervisor` 判断触发了以下任一条件：
   - 当前 round 已接受，需要短周期收口
   - runtime 中断后需要恢复
   - 到达维护时间窗口，例如每几小时一次
2. `supervisor` 写给 `cleanup-agent` 的 cleanup handoff
3. `cleanup-agent` 返回 cleanup report
4. `supervisor` 决定：
   - 继续主工作回环
   - 生成新的维护切片
   - 打开 decision gate

## 8. 自动处理普通问题

### 8.1 默认要求

Harness 必须先消化普通问题，而不是把它们直接甩给人。

至少这些问题应该默认自动处理：

- 路径或文件名不明确，但仓库里能搜到
- 上一步 report 已经给出答案
- 需要选择最小验证命令
- 需要补 handoff / report 的格式字段
- runner 失败后的短时重试

### 8.2 默认策略来源

自动答案只能来自：

- 当前 handoff
- 当前 mission
- 当前 repo 状态
- 上一步 report
- 已有 answer
- role 默认策略

### 8.3 role 默认策略

- `design-agent`
  - 先延续当前切片，不扩大范围
- `execution-agent`
  - 先做最小主线改动，不保留额外 fallback
- `audit-agent`
  - 先要求 focused verification，不直接问人
- `cleanup-agent`
  - 先保留证据，不擅自删重要内容
- `communication-agent`
  - 不把 routine blocker 暴露给人

## 9. 恢复与长时间运行

长时间运行至少要支持：

- `main.py` 重启后继续
- runner 中断后继续
- 同一个 handoff 不重复消费
- 已回答的问题不重复打开
- 已完成的 report 不重复覆盖
- 同一个 gate 不重复问人
- 定时触发 `cleanup-agent` 的维护模式
- 在安全边界触发维护，而不是打断半个未完成 turn

这部分不需要先做大对象层。
先把 `state.json`、`locks/`、report 检测和 handoff 去重做稳。

## 10. 验证规则

### 10.1 每次改动都要验证

不能只因为 agent 会话结束就当成完成。

每次改动都必须有真实验证结果。

这条规则适用于两类场景：

- 修改 `Harness Engineering` 本身
- Harness 用来完成仓库里的其他任务

也就是说，这是 Harness 的运行规则，不只是 Harness 自身开发时的规则。

### 10.2 完整功能必须有端到端测试

如果一个切片 claim 了完整功能，就必须有端到端测试。

这里的端到端测试指跨越真实边界，而不是只跑单个文件测试。

至少这些情况必须有端到端测试：

- 完整 agent loop
- 完整 question interception
- 完整 human gate
- 完整 resume/retry
- 任何通过 Harness 交付的完整用户功能
- 任何通过 Harness 交付的完整系统能力

### 10.3 关闭条件

一个 handoff 只有在这些条件都满足后才能结束：

- 输出已写出
- 验证已执行
- 结果已写进 report
- `audit-agent` 没有 reopen

这里的 handoff 不限于 Harness 自己的内部改动。
只要该 handoff 代表 Harness 当前承担的任务切片，就要遵守同一关闭条件。

## 11. 具体实现顺序

### 11.1 Step 1: 把 `main.py` 变成真正的 supervisor 状态机

文件：

- 修改 `harness-engineering/main.py`
- 创建 `harness-engineering/lib/scheduler.py`
- 创建 `harness-engineering/lib/runtime_state.py`
- 创建 `harness-engineering/lib/locks.py`

完成标准：

- 支持一次运行
- 支持 watch 模式
- 能初始化 `.harness/`
- 能恢复 `state.json`
- 不再把固定流水线当成唯一调度模型

### 11.2 Step 2: 落地最小 handoff / report / question / answer

文件：

- 创建 `harness-engineering/lib/handoff.py`
- 创建 `harness-engineering/lib/report.py`
- 创建 `harness-engineering/lib/question_router.py`
- 创建 `harness-engineering/tests/test_runtime_files.py`

完成标准：

- agent 可以收到 handoff
- agent 可以写 report
- agent 可以写 question
- `supervisor` 可以写 answer

### 11.3 Step 3: 落地 runner 和 side-channel

文件：

- 创建 `harness-engineering/runners/codex_app_server.py`
- 创建 `harness-engineering/lib/runner_bridge.py`
- 创建 `harness-engineering/tests/test_runner.py`

完成标准：

- runner 可以启动 agent
- runner 可以收集 report / question / artifact
- runner 可以恢复上一次 session
- `communication-agent` 作为侧通道而不是固定主链第一步

### 11.4 Step 4: 落地自动处理普通问题

文件：

- 创建 `harness-engineering/lib/auto_answer.py`
- 修改各 agent `system.md`
- 创建 `harness-engineering/tests/test_auto_answer.py`

完成标准：

- 普通问题不会直接问人
- `supervisor` 可以自动给 answer
- 只有 `supervisor` 才能把问题升级给 `communication-agent`

### 11.5 Step 5: 落地 cleanup 双周期和端到端测试

文件：

- 创建 `harness-engineering/tests/test_end_to_end_loop.py`
- 创建 `harness-engineering/tests/test_resume_loop.py`
- 创建 `tests/e2e/test_harness_engineering_long_run.py`
- 创建 `scripts/harness/run-soak.py`

完成标准：

- `design/execution/audit` 主回环可完整跑通
- question interception 可跑通
- human gate 可跑通
- `cleanup-agent` 的 `round-close` 和 `maintenance` 触发都可跑通
- 重启恢复可跑通
- 完整功能切片都有端到端测试

## 12. 完成定义

这轮实现只有在以下条件都满足时才算完成：

- `main.py` 是唯一正式调度入口
- `.harness/` 是唯一活动运行时工作区
- agent 之间通过 handoff / report / question / answer 传信息
- 普通问题默认由 harness 自动处理
- 只有 `communication-agent` 可以问人
- 重启后可以恢复
- 每次改动都经过完整验证
- 完整功能有端到端测试并且实际跑过

## 13. 非目标

本计划当前不做这些事：

- 先设计复杂协议层
- 先设计庞大对象体系
- 先把 runtime 依赖在外部系统边界上
- 让多个 agent 直接对人发问
