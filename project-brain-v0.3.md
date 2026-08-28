# Project Brain / v0.3 主动认知方案

> 版本：v0.3
>
> 状态：第三版实施方案
>
> 核心目标：**让 Brain 从“被动存取记忆”升级为“主动提出可审阅的项目认知”，但不擅自把推断变成事实。**

## 1. 版本定位

v0.2 已经验证了 Project Brain 的可信基础：项目隔离、任务状态、交接恢复、检索
阈值、证据链和 SearchProvider 抽象均已通过测试与破坏性用例。

v0.3 的问题不再是“数据能不能安全存取”，而是：

- Agent 是否必须手动记住每一条值得保存的信息？
- Git、测试和交接事件能否自动沉淀为项目历史？
- Brain 能否识别可能过期、冲突或重复的知识？
- Brain 能否告诉 Agent 哪些事实需要验证、为什么需要验证？
- 项目是否能形成一份随事件演进的、可解释的 Project Model？

v0.3 的边界是“主动建议，人工或 Agent 明确确认后才生效”。核心存储和协议仍然
不依赖某个模型、供应商或远程服务。

## 2. 目标与非目标

### 2.1 目标

1. 自动捕获 Git、测试、文件变更和 handover 等低风险事件。
2. Curator 根据事件和现有记忆提出分类、摘要、去重、冲突和验证建议。
3. 提供 Review Queue，让人或 Agent 批量审阅 proposal。
4. 建立按项目版本、分支和时间变化的 Project Model 快照。
5. 让 onboard 和 ask 使用“已确认记忆 + 明确标注的建议”，不混淆事实与推断。
6. 支持可选的本地模型和 embedding，但关闭后核心行为仍可用、可测试、可复现。

### 2.2 非目标

- 不让 Curator 自动修改代码、任务或正式 Decision。
- 不把所有终端输出、全部 diff 或完整文件内容写入长期记忆。
- 不在本版实现团队权限、远程协同、复杂任务调度和云端模型托管。
- 不以“模型看起来聪明”替代证据、审批和回归测试。
- 不默认启用远程 embedding 或把项目内容发送到外部服务。

## 3. 核心设计决定

### D3-001：Proposal 与事实严格分离

Curator 的所有新增或修改建议都进入 `proposals`，不得直接写入正式 Memory。

```text
event -> curator -> proposal -> review -> apply -> memory/event
```

Proposal 必须带：`project_id`、来源事件、建议动作、目标实体、理由、置信度、
模型/规则版本和创建时间。

### D3-002：默认规则 Curator，可插拔模型 Curator

规则 Curator 是默认实现，负责确定性操作：事件分类、字段抽取、重复候选、证据
缺失、路径/commit 关联和过期候选。

本地小模型只能补充摘要、分类和冲突解释，输出格式与规则 Curator 相同，且只能
生成 proposal。核心协议不直接依赖 LLM。

### D3-003：事实按版本和时间生效

Memory 不仅表示“曾经正确”，还需要表达适用的项目状态：

- `valid_from` / `valid_until`
- `commit` / `branch`
- `supersedes` / `conflicts_with`
- `verification_due_at`

过期候选不会被删除，而是在 ask/onboard 中降级并说明原因。

### D3-004：主动建议必须可解释

每个 proposal 至少能回答：

1. 为什么提出？
2. 来自哪些 Event/Evidence？
3. 影响哪些 Memory/Task？
4. 不确认会有什么风险？
5. 如何验证？

## 4. Project Model

Project Model 是面向当前项目状态的派生视图，不取代底层 Memory、Evidence 和
Event。它由已确认实体和关系计算得到，并且可以按 commit 或时间重建。

### 4.1 第一版模型层

```text
Project Model
├── identity        项目身份、边界、原则
├── current_state   当前目标、阶段、阻塞点
├── architecture    组件、接口、依赖和约束
├── decisions       当前有效决策及其理由
├── risks           已知失败、开放问题、过期证据
├── work            active/blocked task 和最近 handover
└── provenance      每个结论的来源链
```

v0.3 不单独引入图数据库。Project Model 由 SQLite 查询和缓存快照生成，所有字段
都能回到 source ID。

### 4.2 Model Snapshot

增加 `model_snapshots`：

```text
id, project_id, basis_commit, basis_branch, generated_at,
model_json, source_ids, confidence, curator_version
```

快照是可删除、可重建的派生数据；正式事实仍以底层记录为准。

## 5. Event Ingestion

### 5.1 事件来源

优先支持以下可复核来源：

- Git commit、branch、changed files、diff summary；
- 测试命令、退出码、测试摘要和日志路径；
- `brain.record`、`brain.handover`、`brain.verify`、`brain.invalidate`；
- 显式用户确认和 Agent 观察。

每个采集器都输出统一 Event：

```json
{
  "project_id": "project-brain",
  "action": "run_test",
  "source": "make test",
  "result": "passed",
  "commit": "abc123",
  "changed_files": [],
  "agent_id": "agent-a",
  "session_id": "session-1"
}
```

### 5.2 采集原则

- 显式执行或用户开启后才采集；
- 采集器失败只产生 warning，不阻塞主任务；
- 对同一 commit/命令具备幂等键，重复运行不产生无限事件；
- 原始输出保留为 Evidence 引用，摘要才进入 Memory proposal；
- 采集内容默认限定在当前 `project_id` 和当前工作目录。

## 6. Curator 与 Review Queue

### 6.1 规则 Curator 的职责

规则 Curator 在每批新事件后产生候选：

| 触发 | 建议类型 | 示例 |
|---|---|---|
| 测试通过 | experience/knowledge | 某配置在某 commit 下通过 |
| 测试失败 | experience/risk | 某方案在特定条件下失败 |
| 多次修改同一模块 | knowledge | 模块边界或热点风险候选 |
| 新 commit 改变既有约束 | conflict/supersedes | 旧决策可能过期 |
| Evidence 路径或 commit 失效 | verify/invalidate | 证据健康检查 |
| 重复文本或相似事件 | duplicate | 合并或关联候选 |

### 6.2 Proposal 动作

```text
create_memory
update_memory
verify_memory
invalidate_memory
create_link
create_task
append_state_change
```

所有动作默认是 `pending`。审阅结果为：

```text
approved | rejected | deferred | superseded
```

### 6.3 Review Queue 协议

增加：

```text
brain.review_list(project_id, status?, limit?)
brain.review_apply(project_id, proposal_id, action, reviewer, reason?)
brain.curate(project_id, event_ids?, mode?)
```

`review_apply` 必须检查 proposal 的项目边界、来源是否存在、目标实体是否仍为当前
版本，并在 apply/reject 后生成 Event。已经应用的 proposal 不能重复应用。

## 7. 协议演进

### 7.1 `brain.onboard`

增加：

- `project_model_summary`；
- `pending_reviews` 数量；
- `stale_context`；
- `verification_suggestions`；
- 最近一次模型快照的 basis commit。

默认不把 pending proposal 混入 `important_decisions` 和 `known_failures`，只在
单独的 `pending_reviews` 中展示。

### 7.2 `brain.ask`

增加可选参数：

```text
include_proposals: false
as_of_commit: null
as_of_time: null
```

默认只查已确认且在当前时间/版本有效的事实。若包含 proposal，返回中必须标明：

```json
{
  "kind": "fact|proposal|stale_fact",
  "provenance": ["EV-001", "E-001"],
  "verification_suggestion": "在当前分支重新运行 make test"
}
```

### 7.3 `brain.record`

继续支持显式写入，并增加 `origin`：

```text
user | agent | rule_curator | model_curator | importer
```

`model_curator` 不能直接请求 `verified` 或 `active`。任何此类请求都必须转为
proposal 并记录 warning。

### 7.4 `brain.handover`

handover 增加：

- 本次会话产生的 Event IDs；
- 未处理 proposal 数量；
- 建议验证项；
- basis commit/branch；
- 当前 Project Model snapshot ID。

## 8. 检索和模型的关系

v0.3 继续以 FTSProvider 为默认搜索实现。SearchProvider 新增可选上下文参数：

```text
search(project_id, query, scope, limit, as_of_commit?, include_proposals?)
```

embedding provider 仍然是可选实现，只有在固定评测集证明 FTS 无法覆盖的问题上才
启用。语义召回结果必须经过与词法结果相同的 project、status、evidence 和相关度
门槛，不能绕过可信度层。

## 9. 实施阶段

### Phase 1：事件采集与幂等

- 统一 Event payload 和 source metadata；
- 增加 Git/test 显式采集入口；
- 增加事件幂等键和采集 warning；
- 测试重复采集、错误命令和跨项目路径。

### Phase 2：Proposal 与 Review Queue

- 新增 proposals 表和状态机；
- 实现规则 Curator；
- 实现 review_list、review_apply、curate；
- 确保 apply/reject 全部生成可追溯 Event；
- 增加批量 approve/reject 的原子性测试。

### Phase 3：Project Model 与时间版本

- 新增 model snapshot；
- 从 Memory/Link/Event 派生架构、决策、风险和工作视图；
- 增加按 commit/time 重建；
- 标记 stale evidence 和 stale memory；
- onboard 输出模型摘要和验证建议。

### Phase 4：可选本地模型

- 定义 ModelCurator adapter；
- 约束输入输出 schema、超时、token budget 和本地路径；
- 模型失败自动回退规则 Curator；
- 模型结果只进入 proposal；
- 建立规则结果与模型结果的离线对比集。

### Phase 5：可选混合检索

- 接入离线 embedding provider；
- 比较 FTS、bigram 和 embedding 的召回差异；
- 只在事实过滤和 provenance 校验之后合并结果；
- 记录模型/索引版本，保证结果可复现。

## 10. 测试与验收

### 10.1 必须通过的场景

1. 同一 commit 或测试命令重复采集只产生一个逻辑 Event。
2. Event、Evidence、Proposal、Memory 全部保持 project_id 隔离。
3. 规则 Curator 能从测试失败提出 experience/risk proposal。
4. Proposal 未审核前不会出现在 facts、important_decisions 或 verified memory 中。
5. approve、reject、defer、supersede 都会生成 Event 且可重放查询。
6. 目标实体或证据在 proposal 产生后变化时，apply 会拒绝或要求重新审阅。
7. 旧 commit 下的 Decision 与新 commit 下的 superseding Decision 可以分别查询。
8. 过期 Evidence 会被标注 stale，不会静默支撑当前结论。
9. Project Model snapshot 可以从底层数据删除并重建，结果 source IDs 一致。
10. 本地模型不可用时，规则 Curator 和四个 v0.2 核心协议仍然全部可用。
11. onboard 默认不混入 proposal；显式请求时能区分 fact/proposal/stale_fact。
12. embedding provider 返回异常、低分或跨项目结果时，不影响默认 FTSProvider。

### 10.2 质量指标

在固定的事件回放集上记录：

- proposal precision：被审核通过的 proposal 比例；
- stale detection recall：被识别的过期证据/记忆比例；
- provenance coverage：模型摘要可追溯到 source ID 的比例；
- duplicate suppression：重复事件未产生重复长期记忆的比例；
- review burden：每 100 个事件产生的待审 proposal 数量；
- fallback availability：模型不可用时核心协议成功率。

v0.3 第一目标不是最大化 proposal 数量，而是让 proposal 足够少、足够有依据、
足够容易审阅。关键底线是：无来源 proposal 不得自动应用，跨项目 proposal 为 0。

## 11. 交付物

- `project-brain-v0.3.md`：本方案；
- Event ingestion 接口、Git/test 采集器和幂等测试；
- proposals 表、Curator、Review Queue 和审阅 CLI/MCP 工具；
- Project Model snapshot 与按版本重建能力；
- stale evidence/knowledge 检查；
- 可选 ModelCurator adapter，但默认不启用外部模型；
- 更新后的 onboard/ask/handover 返回 schema；
- 事件回放评测集与 v0.3 验收报告。

## 12. 最终判断

v0.3 完成的标准不是“Brain 自动写了很多东西”，而是：

> **Brain 能从项目事件主动提出少量、有证据、可解释、可审阅的认知更新；即使所有自动化关闭，v0.2 的可信交接能力仍完整保留。**

这会为后续团队协作、任务租约和更强语义检索建立安全基础。
