# Project Brain / v0.5 Workflow Brain 方案

> 版本：v0.5
>
> 状态：第五版实施方案
>
> 核心目标：**让个人 Agent 不必主动管理 Brain，Brain 仍能在不越权的前提下自动沉淀工作上下文。**

## 1. 版本定位

v0.4 已经把 Brain 做成可迁移、可诊断、可备份、可并发运行的本地基础设施。v0.5
不继续扩展团队权限、远程同步或多人协作，而是把 Brain 嵌入个人 Agent 的工作流。

当前使用方式仍然要求 Agent 主动决定：

```text
什么时候 onboard？
什么时候 ingest？
什么时候 curate？
什么时候 handover？
```

Workflow Brain 要把这些动作收敛到一个小而深的工作流模块中。外部 Agent 只需理解
会话开始、观察事件、会话结束三个动作；事件去重、证据采集、Curator、proposal、
快照和 handover 草稿都隐藏在模块实现之后。

## 2. 目标与非目标

### 2.1 目标

1. Agent session 开始时自动提供轻量、相关、可追溯的启动上下文。
2. Git、测试、文件和显式 Agent 观察可以低干扰地自动沉淀为 Event/Evidence。
3. 会话结束、暂停或切换任务时自动形成 handover 草稿。
4. 自动化分为观察、建议、生效三个等级，默认不越过可信度边界。
5. 失败、超时、非 Git 目录和 Brain 不可用时，Agent 主流程仍可继续。
6. 用户可以看到、审阅、撤销任何自动化产生的内容。
7. 通过体验指标验证“无感”确实减少手动操作和上下文恢复成本。

### 2.2 非目标

- 不做多人协作、团队权限、远程同步或多租户。
- 不拦截或修改 Agent 的代码、命令和测试行为。
- 不自动批准 Proposal，不自动把模型输出标记为 verified。
- 不默认保存完整终端输出、完整 diff 或敏感文件内容。
- 不要求 Agent Runtime 必须支持某个特定厂商协议。
- 不以后台自动化次数作为主要成功指标。

## 3. 核心设计决定

### D5-001：WorkflowBrain 是唯一外部工作流接口

WorkflowBrain 是一个深模块，外部接口保持最小：

```text
start_session(context) -> SessionStart
observe(observation) -> ObservationReceipt
end_session(context) -> HandoverDraft
```

接口必须定义的不只是参数，还包括：

- 默认异步/非阻塞行为；
- 幂等键和重试规则；
- 哪些错误会返回 warning，哪些错误会中止操作；
- 自动化等级和写入权限；
- 输出中的 source、evidence、proposal 和 confidence 语义。

CLI、MCP、Git Hook、Test Runner 和 Agent Runtime 都是 Adapter，不直接拼接底层
`ingest/curate/review/snapshot` 流程。

### D5-002：默认 L0/L1，关闭 L2

```text
L0 observe  -> 自动写 Event/Evidence
L1 suggest  -> 自动生成 pending Proposal
L2 apply    -> 只有显式确认才修改正式 Memory/Task/Decision
```

用户可以按项目配置关闭 L1，但不能让模型 Curator 绕过审阅直接写入 verified 或
active 事实。

### D5-003：自动化不阻塞主任务

WorkflowBrain 的自动采集失败、Curator 超时、索引不可用或 handover 草稿生成失败，
都必须返回结构化 warning，并允许 Agent 继续工作。只有显式的 `apply`、`verify`、
`invalidate` 等用户意图操作失败时，才返回硬错误。

### D5-004：所有自动行为可见、可撤销、可重放

每个自动动作都必须带：

```text
automation_run_id
project_id
session_id
trigger
origin
source_ids
created_at
```

自动生成的 Event/Evidence/Proposal 必须能通过一次查询定位，并能从 Event 重放出
相同的 Proposal 结果。

## 4. 会话生命周期

### 4.1 Session Start

`start_session` 完成：

1. 识别 project root、project_id、branch 和 commit；
2. 创建或恢复 Session；
3. 返回轻量启动上下文；
4. 标记最近的 handover、blocker、stale context 和 verification suggestion；
5. 记录 session_start Event。

默认启动上下文只包含：

```json
{
  "identity": "...",
  "current_goal": "...",
  "next_step": "...",
  "blockers": [],
  "critical_warnings": [],
  "pending_reviews": 0,
  "stale_context": 0,
  "basis_commit": "...",
  "source_ids": ["S-001", "T-001"]
}
```

完整 Project Model、失败经验和历史决策仍通过现有 onboard/ask 按需展开。

### 4.2 Observe

`observe` 接收标准化 Observation：

```json
{
  "kind": "git|test|file|agent_note|command",
  "source": "make test",
  "result": "passed",
  "payload": {},
  "session_id": "session-1",
  "idempotency_key": "..."
}
```

内部流程为：

```text
observation -> normalize -> deduplicate -> persist Event/Evidence
             -> optional curate -> return receipt
```

默认只在低成本触发点执行 Curator；高成本模型 Curator 不应阻塞 observe。

### 4.3 Session End

`end_session` 自动汇总：

- session 期间的 Event/Evidence；
- 已确认的 Memory 变化；
- pending Proposal；
- 当前 Task 状态；
- 未完成内容和 blocker；
- basis commit/branch；
- Project Model snapshot。

返回 `HandoverDraft` 并写入草稿记录。默认不直接覆盖正式 State；用户或 Agent 明确
确认后才应用为正式 handover。

## 5. 自动化触发器

### 5.1 触发器层级

| 触发器 | 默认 | 自动结果 | 风险 |
|---|---:|---|---|
| session start | 开启 | 轻量 onboard | 低 |
| Git commit/status | 开启 | Event/Evidence | 低 |
| 测试开始/结束 | 开启 | Event/Evidence | 低 |
| 文件变更摘要 | 可选 | Event | 中 |
| Agent 显式 note | 开启 | Event/Proposal | 中 |
| session end | 开启 | handover draft | 中 |
| model curator | 关闭 | Proposal | 高 |

### 5.2 触发策略

- 默认只观察当前项目和当前 session；
- 文件变更采用 debounce，避免每次保存都写入事件；
- 测试按 command + commit + working tree fingerprint 去重；
- Git hook 是可选安装，不要求修改用户已有工作流；
- Agent Runtime 无法提供 hook 时，CLI/MCP 显式调用仍然完整可用。

## 6. 可信度和隐私边界

### 6.1 自动写入白名单

默认允许自动写入：

- session、Git、测试和采集状态 Event；
- 指向日志、commit 和相对路径的 Evidence；
- `observed` 状态的观察记录；
- `pending` Proposal。

默认禁止自动写入：

- `verified` Decision；
- `active` 结论；
- 删除或覆盖已有 Memory；
- 修改用户明确确认过的 State；
- 读取未列入允许范围的敏感文件。

### 6.2 敏感内容处理

配置支持：

```text
exclude_paths
redact_patterns
max_payload_bytes
retain_raw_output=false
```

命令输出默认保存摘要、退出码、耗时和 Evidence locator；密钥、token、环境变量和
匹配 redact pattern 的内容不得进入 Event、Evidence 或 Proposal。

### 6.3 可见性

每次 start/observe/end 都返回：

- 是否实际写入；
- 写入了哪些类型；
- 生成了多少 proposal；
- 哪些动作被跳过及原因；
- warning 和降级路径。

“无感”表示不要求 Agent 额外决策，不表示系统隐藏自己的行为。

## 7. 数据模型扩展

### 7.1 Session

新增 `sessions`：

```text
id, project_id, agent_id, started_at, ended_at,
basis_commit, basis_branch, status, last_event_at,
automation_mode, metadata_json
```

状态：

```text
active | paused | completed | abandoned
```

### 7.2 Automation Run

新增 `automation_runs`：

```text
id, project_id, session_id, trigger, status,
started_at, finished_at, created_ids, warnings, error
```

状态：

```text
running | completed | partial | failed | skipped
```

它是运行审计，不是项目事实，不进入默认 onboard facts。

### 7.3 Handover Draft

正式 handover 之外增加 draft 状态，包含：

- draft 内容；
- source Event IDs；
- proposal IDs；
- basis commit；
- generated_by；
- applied_handover_id（应用后填写）。

草稿可以被重新生成，但已应用的正式 handover 不可被静默覆盖。

## 8. Agent 体验接口

### 8.1 启动上下文策略

启动上下文分为三级：

1. `compact`：身份、目标、下一步、blocker、严重 stale 和 source IDs；
2. `focused`：compact 加当前任务、相关决策、失败经验和最近 handover；
3. `full`：现有 onboard 完整交接包。

默认使用 compact；用户或 Agent 提供 focus 时使用 focused；只有明确请求才使用 full。

### 8.2 结束时的最小输入

Agent 不必重新填写完整 handover，只需可选提供：

```json
{
  "summary": "完成了什么或遇到了什么",
  "next_step": "下一步",
  "status": "partial"
}
```

其余内容由 WorkflowBrain 从 session Event、Task 和 Proposal 推导，并明确标注为
observed、inferred 或 proposed。

### 8.3 手动控制

保留显式开关：

```text
brain workflow status
brain workflow pause
brain workflow resume
brain workflow flush
brain workflow explain --run RUN-001
brain workflow undo --run RUN-001
```

pause 只停止自动触发，不删除历史；flush 用于把待处理事件写入数据库；explain 展示
自动化来源；undo 只撤销尚未被后续事实依赖的自动产物。

## 9. 失败与降级策略

```text
Agent task
   │
   ├─ WorkflowBrain 可用 ──> observe/curate/handover draft
   │
   └─ WorkflowBrain 异常 ──> warning + local buffer
                              │
                              ├─ flush 成功后补写
                              └─ 用户明确 flush 失败，主任务仍继续
```

本地 buffer 必须：

- 限制大小和保留时间；
- 使用项目和 session 绑定；
- 不保存被隐私策略拒绝的原始内容；
- 重复 flush 幂等；
- 明确显示尚未落盘的数量。

## 10. 实施阶段

### Phase 1：WorkflowBrain 核心模块

- 定义 start/observe/end interface 和结果模型；
- 增加 Session、Automation Run、Handover Draft；
- 将现有 ingest/curate/handover 编排收进内部 implementation；
- 保留 v0.4 协议作为兼容 Adapter；
- 建立 in-memory adapter 和故障注入测试。

### Phase 2：无感观察

- 接入 Git、test、file、agent_note 触发器；
- 实现 debounce、幂等和 local buffer；
- 增加 exclude/redact/max payload 配置；
- 非 Git 目录、命令失败和 Brain 不可用时安全降级。

### Phase 3：启动与结束体验

- compact/focused/full 三种上下文；
- 自动 session start 和 session end；
- handover draft 生成、预览、确认和撤销；
- workflow status/pause/resume/flush/explain。

### Phase 4：可信度与可解释性

- 自动化等级策略；
- 每个自动动作的 source/provenance；
- 自动产物批量查看和 undo；
- proposal 噪声、误采集和敏感信息回归测试。

### Phase 5：真实 Agent 试用

- 在连续多次个人 session 中运行；
- 对比手动 Brain 调用和自动触发的成本；
- 记录上下文恢复、handover 质量、proposal 采纳和噪声；
- 只根据试用数据调整默认触发器，不提前扩大自动权限。

## 11. 测试与验收

### 11.1 功能场景

1. session start 返回 compact 上下文，并带 source IDs。
2. 同一 Git/test observation 重复提交只产生一个逻辑 Event。
3. 多种触发器产生的 Event/Evidence 保持 project_id 和 session_id 正确。
4. 测试失败只产生 observed Event、Evidence 或 pending Proposal，不直接产生 verified fact。
5. session end 能生成包含完成项、剩余项、下一步和来源的 handover draft。
6. handover draft 未确认前不覆盖正式 State；确认后可追溯到 draft。
7. pause 后不再自动采集，resume 后可继续且不重复写入。
8. WorkflowBrain 不可用时，Agent 主任务成功，warning 和 buffer 状态可见。
9. flush 重试后不重复写入，数据完整落盘。
10. `exclude_paths` 和 `redact_patterns` 能阻止敏感内容进入所有长期记录。
11. `explain` 能展示一次自动运行的触发、来源、产物和 warning。
12. `undo` 不破坏后续已确认事实，并生成审计 Event。
13. compact/focused/full 的 token 预算和字段优先级稳定。
14. v0.4 的 onboard/ask/record/handover/ingest/curate/review/snapshot 继续兼容。
15. Git hook、CLI、MCP 三种 Adapter 的核心结果一致。

### 11.2 体验指标

在固定的个人项目 session 集上记录：

- Agent 手动调用 Brain 的次数；
- session start 到获得有效上下文的耗时；
- 自动生成 handover 被采用或修改的比例；
- proposal 采纳率与噪声率；
- 每次 session 产生的 Event、Evidence 和 Proposal 数量；
- handover 后重复探索项目的时间；
- 自动化 warning、buffer 和 flush 失败率；
- 敏感内容误采集次数。

质量底线：

- 自动生成 verified fact：0；
- 敏感内容进入长期记录：0；
- 自动化跨项目污染：0；
- 主任务因 WorkflowBrain 故障被阻塞：0；
- v0.4 兼容协议回归通过率：100%。

## 12. 最终判断

v0.5 的完成标准不是“自动化写入更多记忆”，而是：

> **Agent 可以像平时一样工作，Brain 在后台安静地记录必要事实、提出可审阅建议，并在下一次会话开始时给出刚刚好的上下文；它的每一步都可见、可解释、可撤销。**

这一步完成后，Brain 才真正从一个需要 Agent 主动调用的工具，变成个人 Agent 工作流
中的默认认知层。
