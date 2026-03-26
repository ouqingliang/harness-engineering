# 2026-03-26 Harness Engineering 代码现状盘点

## 1. 先说结论

基于 `memory/` 里的基线文档来看，当前仓库已经有了一套“最小可运行”的 Harness Engineering 骨架，但它还没有达到最初设计里那种真正长期自治、角色边界清晰、靠真实执行和真实验收闭环的完成状态。

更准确地说：

- 作为一个最小 demo/runtime skeleton，它已经可以跑起来
- 作为“按最初设计落地的 Harness Engineering”，它还没有完成
- 现在最像的是“由 `scheduler` 内建角色行为的单进程模拟闭环”
- 还不像“真正由各角色 agent 通过 handoff/report/question/answer 驱动的长期运行时”

## 2. 我是怎么理解这套 Harness 的

先按 `memory` 基线文档来抽象，这个仓库里 `Harness Engineering` 的意思不是新的产品子系统，而是一个包在 agent 外面的执行壳。

它的目标是：

- 用固定主链让 agent 能持续工作
- 把运行时状态稳定落到 `<memory_root>/.harness/`
- 把普通 blocker 留在 harness 内部自动消化
- 只把真正的 decision gate 暴露给人
- 不允许在没有真实验证证据的情况下把任务当成完成

文档里定义的最小主链是：

1. `communication`
2. `design`
3. `execution`
4. `audit`
5. `cleanup`

其中 `supervisor` 负责 orchestration truth，但它更像调度拥有者，而不是一个必须被单独执行的业务角色。

## 3. 当前代码的实际架构

### 3.1 入口层

唯一正式入口确实是 `main.py`。

它当前提供 4 个命令：

- `inspect`
- `run`
- `reply`
- `status`

主要流程是：

1. 从 `config.yaml` 读取配置
2. 从 `--doc-root` 扫描 UTF-8 文档，生成 `doc_bundle`
3. 初始化或恢复 `<memory_root>/.harness/`
4. 从 `agents/*-agent/agent.json` 读取 5 个 agent spec
5. 创建 `HarnessScheduler`
6. 由 `HarnessScheduler` 驱动固定链路
7. 如有 gate，则通过 communication surface 等待人工回复

这里有一个很重要的现实点：`main.py` 实际只加载 `*-agent/agent.json`，所以 `agents/supervisor/` 不会进入 `inspect` 输出，也不会作为一个独立 agent 被跑起来。`supervisor` 的职责是内嵌在调度器里的。

### 3.2 调度层

真正的核心不在 `agents/`，而在 `lib/scheduler.py`。

`HarnessScheduler` 做了这些事：

- 持有 mission/state
- 生成每一步 handoff
- 调用 `RunnerBridge.run_agent(...)`
- 收集 report
- 路由 question
- 自动写 answer
- 在需要时打开 human gate
- 推进 active agent 和 round

也就是说，真正的 orchestration truth 的确在 supervisor 一侧，但当前 carrier 是 `HarnessScheduler` 这个 Python 类，而不是独立的 `supervisor` agent。

### 3.3 runner / communication 层

`lib/runner_bridge.py` 和 `runners/codex_app_server.py` 提供了一个很薄的 runner 壳：

- 写 handoff JSON
- 写 report JSON
- 保存 launcher state
- 提供 `/health` `/runtime` `/communication/messages` `/communication/gates` `/run` `/run-agent` 等 HTTP 接口

`lib/communication_api.py` 则负责：

- 打开 gate
- 记录 message
- 接受人工 reply
- 把人工回复写进 `.harness/answers/*.json`

这一层是能工作的，而且和文档里的“统一 communication surface”方向基本一致。

### 3.4 runtime 文件层

`.harness/` 的目录骨架已经落地：

- `mission.json`
- `state.json`
- `handoffs/`
- `reports/`
- `questions/`
- `answers/`
- `artifacts/`
- `locks/`
- `launchers/`

UTF-8 读写也基本一致，`runtime_state.py` / `documents.py` / `communication_api.py` 都明确按 UTF-8 处理文本。

## 4. 现在“能不能运行整个 Harness Engineering”

### 4.1 可以运行到什么程度

如果问题是“这套代码有没有最小可运行闭环”，答案是：有。

我做了 fresh verification：

- 使用本机可用的 Python 解释器  
  `C:\Program Files\Lenovo\ModelMgr\Plugins\Image\python.exe`
- 运行 `-m unittest discover -s tests -v`
- 结果：10 个测试全部通过

其中覆盖了：

- runtime 文件 round-trip
- runner bridge
- auto answer
- end-to-end loop
- gate wait/resume
- HTTP communication surface
- CLI `main.py run`

所以，“最小闭环能跑”这件事是有证据支持的。

### 4.2 不能简单说“已经完全可以”

如果问题是“按现在仓库的真实 `memory/` 文档，整套 Harness Engineering 是否已经能像设计里那样顺滑运行完”，答案是：还不能直接这样说。

我直接拿仓库自己的 `memory/` 作为 doc root 跑调度器，结果是：

- `doc_count = 4`
- `gate_signal_count = 11`
- 首次运行状态是 `waiting_human`

也就是说，面对当前真实基线文档，系统不是自动完成，而是会进入人工 gate 等待。

再进一步看，这 11 个 gate signal 并不都是真正“需要决策的架构问题”，很多只是文档本身在描述 decision gate policy、security boundary、destructive action 这些规则。当前实现把“提到这些词”也算成 gate signal，所以对真实设计文档会产生明显误报。

不过，一次人工回复之后，当前实现确实可以继续跑完到 `completed`。这说明它有“停住 -> 回复 -> 恢复”的骨架，但 gate 判定还很粗糙。

### 4.3 运行层面的额外现实问题

仓库文档和测试默认都写的是 `python ...`，但我当前这个环境里没有可直接工作的 `python` / `py` 命令，只有一个可用的显式 Python 路径。因此：

- 代码本身可以运行
- 但“照 README 里的命令直接运行”在这台机器上不成立

这属于环境问题，不完全是仓库代码问题，但对“现在能不能直接跑”这个判断是有影响的。

## 5. 当前主链路到底是怎么走的

现在的实际链路可以概括成下面这条：

1. `main.py run --doc-root ...`
2. `build_doc_bundle(doc_root)` 扫描 UTF-8 文档
3. `ensure_runtime_root(memory_root)` 初始化 `.harness/`
4. `load_or_reset_runtime(...)` 生成/恢复 `mission.json` 和 `state.json`
5. `load_all_specs()` 从 `agents/*-agent/agent.json` 读出 5 个 agent
6. `HarnessScheduler.run_until_stable()` 进入固定链路
7. 每一步由 `_build_handoff()` 生成通用 handoff
8. `RunnerBridge.run_agent(...)` 写入 handoff/report 文件
9. 实际“角色行为”由 `HarnessScheduler._execute_turn(...)` 直接内嵌执行
10. 如果 report 里带 question，`_route_questions()` 判断是 auto-answer 还是 gate
11. ordinary blocker 走 `auto_answer.py` 自动答复
12. 真正 gate 走 `CommunicationStore.open_gate(...)`
13. 人工回复写入 `.harness/answers/*.json`
14. scheduler 检测 reply 后继续推进链路
15. `cleanup` 把 round 标记为 completed 或 reopen 到 execution

所以当前架构的本质不是“多个真正独立的 agent 进程/线程协作”，而是：

- 一个 scheduler
- 一个 runner bridge
- 一组 JSON runtime 文件
- 加上一段内嵌在 scheduler 里的角色模拟逻辑

## 6. 还没有完成的地方

下面这些我认为都还没有真正完成，而且其中几项是结构性缺口，不只是“小优化”。

### 6.1 `execution-agent` 还是占位实现

`execution` 现在没有做真实执行，它只是写了一个 execution plan artifact，内容大致是：

- 初始化 `.harness/`
- 跑固定 agent chain
- 暴露 communication surface
- 给出一个 `verification_plan`

但它没有：

- 改代码
- 跑验证
- 收集真实验证证据
- 把真实执行结果交给 audit

这和文档里“execution 负责主执行工作”还有明显距离。

### 6.2 `audit-agent` 还没有做真正验收

`audit` 当前的 accept/reopen 逻辑非常薄。它主要做的是：

- 读取 execution artifact
- 看里面有没有 `verification_plan`
- 只要 plan 非空，就直接 `accepted`

这意味着它检查的是“有没有计划”，不是“有没有证据”。

而 memory 基线里要求的是：

- execution 要留下 verification evidence
- audit 要拒绝没有 evidence 的完成声明
- 完整能力要有端到端验证

这块当前还没有真正落地。

### 6.3 `cleanup-agent` 还没有做真正的压缩/清理

`cleanup` 现在主要是写一个 summary artifact，然后根据 audit 状态决定：

- `completed`
- 或者回到 `execution`

但它还没有真正实现文档里想要的：

- drift cleanup
- stale artifact cleanup
- durable memory compression
- resume state trimming

### 6.4 `locks` / 去重 / 幂等恢复没有真正做稳

实现计划里明确提到过：

- `lib/locks.py`
- handoff 去重
- report 覆盖保护
- gate 不重复提问
- 重启后的稳定恢复

但当前仓库里：

- `locks/` 目录会创建
- 但 `lib/locks.py` 源文件不存在
- 没有看到真正的 lock / lease / 去重策略落地

因此“长时间运行 + 重启恢复 + 幂等安全”这部分还没有真正达标。

### 6.5 soak / 长跑验证没有完成

实现计划里写了要补：

- `scripts/harness/run-soak.py`

但这个文件现在并不存在。

也就是说，长时间运行时真正最关键的 soak 验证还没有补上。

## 7. 和最初设计不符合的地方

这是这次盘点里最重要的一部分。

### 7.1 角色定义在 `agents/`，但角色行为实际上写死在 `scheduler`

文档最初表达的是：

- 角色边界清晰
- 通过 handoff/report/question/answer 协作
- 各角色是独立的运行时角色

但当前实现里，`communication/design/execution/audit/cleanup` 的核心逻辑都直接写在 `HarnessScheduler._execute_turn(...)` 里。

`agents/*/system.md` 和 `agent.json` 目前更像静态说明，而不是实际运行时会被 runner 读取、装配并执行的角色定义。

这意味着：

- 角色“目录结构”是对的
- 角色“运行机制”还没有真正从调度器里拆出来

### 7.2 `supervisor` 是文档角色，不是实际被加载的 agent

memory 文档一直把 `supervisor` 定义成核心 owner，但 `main.py` 只会 glob `*-agent/agent.json`。

结果就是：

- `supervisor` 目录存在
- 但不参与 `inspect` 输出
- 也不作为独立 agent 进入执行链

当前真实情况是：`supervisor == HarnessScheduler`

这并非一定错误，但和“所有角色都在 agents 下有统一载体”的表达存在偏差。

### 7.3 主链 cycle 被切碎了，不是一个完整 round 的连续 runner cycle

这是我觉得最值得修的一个实现问题。

`RunnerBridge.run_agent(...)` 本身支持：

- `cycle_id`
- `sequence`

按理说，一个完整 round 应该共享同一个 cycle，然后 sequence 从 0 往后加。

但 `HarnessScheduler._run_agent_until_stable(...)` 每次都把 `self.state.to_mapping()` 直接传给 `run_agent(...)`，而这个 state 里没有 runner 的 `cycle_id/sequence`。

结果就是：

- 每个 agent turn 都会生成自己的 `cycle-*`
- 很多 report 文件名都变成 `cycle-xxxx-00-<agent>.json`
- 整个 round 在 runner 视角下被拆成了多个单步 cycle

这和“一个固定主链 round”的实现意图并不一致，也会让恢复、追踪、对账变得更难。

### 7.4 gate 检测和最初“只在真正决策时升级”不一致

最初设计强调的是：

- 只有真正 decision gate 才升级给人
- routine blocker 不要打扰人

但当前 `documents.py` 的 gate 检测是按关键词扫全文，例如：

- `decision gate`
- `security boundary`
- `destructive`
- `external side effect`
- `goal conflict`

这样一来，只要文档在讲这些原则，也会被识别成 gate signal。

这导致系统在读自己这套架构文档时，就容易把“规则说明”误判成“当前必须人工裁决的事项”。

### 7.5 `config.yaml` 里有些配置没有真正接入主逻辑

现在能看到几个配置项已经写出来了，但没有真正形成可控行为，例如：

- `default_launcher`
- `decision_gate_tags`

`decision_gate_tags` 最终没有驱动 question routing，真正路由还是走 `question_router.py` 里写死的 `DECISION_GATE_TAGS`。

这说明配置层和运行层还没有完全打通。

### 7.6 `run` 的默认行为比文档更“粘住”

README 把 `--watch --serve` 描述成增强模式，但 `config.yaml` 默认就是：

- `watch: true`
- `serve_communication: true`

同时 CLI 只有 `--watch` / `--serve`，没有 `--no-watch` / `--no-serve`。

所以实际效果是：

- 简单执行 `run`
- 在出现 gate 时就可能进入等待和服务模式

这和很多人对“一次 run 命令先跑一轮然后退出”的直觉不太一致。

### 7.7 `handoff.py` / `report.py` 更像测试覆盖对象，不是主链核心载体

文档上说主链要靠 handoff/report/question/answer 传信息，这个方向没错。

但当前主链里：

- `question/answer` 确实有在实际使用
- `handoff/report` 虽然也落文件
- 但真正的核心写入路径主要在 `runner_bridge.py` 的内部 helper
- `lib/handoff.py` 和 `lib/report.py` 反而更像给 round-trip 测试准备的 API

这说明“概念上的 runtime primitive”和“实际主链使用的 primitive”还没有完全合一。

## 8. 哪些部分已经比较对路

虽然上面说了很多缺口，但也不是说这套代码没价值。下面这些方向我认为是已经走对了的：

- `main.py` 作为唯一正式入口，方向是对的
- `.harness/` 作为唯一活动运行时命名空间，方向是对的
- communication surface 与 `.harness/answers/` 联动，方向是对的
- question -> auto answer / gate 的分流骨架已经存在
- 以 memory/docs 作为 mission 输入，而不是靠聊天上下文硬顶，方向是对的
- 测试已经覆盖了最小闭环、HTTP、resume，这比“只有 README 没有测试”要扎实得多

所以这不是一个从零开始的空架子，而是一个“骨架已成，但真正业务化自治能力还没填进去”的状态。

## 9. 我对当前状态的总体判断

如果用一句话概括：

当前仓库已经实现了 Harness Engineering 的“运行时骨架”和“最小演示闭环”，但还没有实现“真正按设计工作的长期自治 harness”。

更细一点，可以这样分级：

- `L1: 目录和角色命名`
  - 基本到位
- `L2: 最小 runtime 文件骨架`
  - 已到位
- `L3: 固定主链调度`
  - 已到位
- `L4: 普通 blocker / human gate 骨架`
  - 已到位，但 gate 误报严重
- `L5: 真正角色化执行`
  - 未完成
- `L6: 真正 verification evidence + audit closure`
  - 未完成
- `L7: 长时间运行的幂等恢复与 soak 可靠性`
  - 未完成

所以现在不适合说“整个 Harness Engineering 已经 fully ready”，更适合说：

- 已经可以做最小闭环演示
- 已经可以作为下一轮真实实现的 runtime carrier
- 但还不能把它当成最终完成形态

## 10. 我建议的下一步

如果下一轮要把它往“真正符合设计”推进，我建议优先级如下：

1. 先把 `execution` / `audit` 从“写计划、看计划”升级成“执行真实动作、校验真实证据”
2. 把 `HarnessScheduler._execute_turn(...)` 里的角色逻辑逐步下沉成真正可替换的 role runner，而不是一直内嵌在调度器里
3. 修正 runner cycle 继承，让一个 round 真正共享 `cycle_id/sequence`
4. 把 gate 检测从“全文关键词扫描”改成“显式标记或结构化 decision marker”
5. 补上 `locks`、去重、重启恢复、soak 验证，特别是 `run-soak.py`
6. 把 `config.yaml` 里声明的配置真正接入运行逻辑
7. 给 CLI 增加 `--no-watch` / `--no-serve` 之类的显式控制，降低默认运行的歧义

## 11. 本次核对使用的事实依据

本次结论基于以下事实：

- 先按 UTF-8 阅读了 `memory/index.md` 与 3 份 baseline 文档
- 再阅读了 `main.py`、`lib/*.py`、`agents/*`、`runners/*`、`tests/*`
- fresh 跑过测试：`10 tests OK`
- fresh 验证过：
  - 简单 doc root 可以完成主链
  - 仓库自带 `memory/` 会进入 `waiting_human`
  - 一次人工回复后可以恢复到 `completed`

因此，这份判断不是只看 README 做的静态推测，而是结合了文档、代码和实际运行结果。
