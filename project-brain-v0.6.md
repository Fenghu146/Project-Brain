# Project Brain / v0.6 Evidence-grounded Answer Brain 方案

> 版本：v0.6
>
> 状态：第六版实施方案
>
> 核心目标：**让 Agent 得到最相关、最权威、最新且可追溯的刚好够用的回答。**

## 1. 版本定位

v0.5 已经证明 Workflow Brain 可以把 session start、事件观察和 session end 嵌入
个人 Agent 工作流。当前主要问题从“是否能找到记录”变成了“找到很多记录后，是否能
只回答真正的问题”。

第五版测试暴露出三类典型现象：

- 查询核心目标时混入版本决策、任务和历史内容；
- 查询方案功能时返回过多事实，召回全面但答案边界偏宽；
- 测试总结、代码块和源码文件名可能作为普通知识进入答案，造成知识污染；
- 回答可以很准确，但 confidence 与 evidence coverage 不完全一致。

v0.6 将重点放在 Answer Brain：在现有 SearchProvider、Project Model、Evidence、
Proposal 和 WorkflowBrain 之上，增加一个深的回答模块。它不重新定义事实，不替代
Curator，而是负责问题理解、来源选择、结果聚合、可信度解释和答案呈现。

## 2. 目标与非目标

### 2.1 目标

1. 根据问题意图选择合适的实体类型、来源层级和时间范围。
2. 让答案正文只包含与问题直接相关的结构化内容。
3. 将 supporting facts、evidence、related context 和 uncertainties 分层返回。
4. 让每个关键断言都能追溯到 Memory、Evidence、Event 或 Project Model source。
5. 校准 confidence，使其反映检索质量、来源权威、证据覆盖、时效和冲突。
6. 识别并降低版本文档、代码块、重复测试总结等知识污染。
7. 在没有模型、embedding 或 Answer Brain 失败时，安全回退到 v0.5 ask 行为。

### 2.2 非目标

- 不自动新增 verified Memory。
- 不把 Answer Brain 变成新的事实存储层。
- 不默认引入向量数据库或远程模型。
- 不追求每个问题都返回固定数量的 facts。
- 不为了生成更自然的文字而隐藏来源、冲突或不确定性。
- 不在本版扩展团队协作、权限和远程同步。

## 3. 核心设计决定

### D6-001：AnswerBrain 是回答编排的唯一外部接口

增加一个小而深的模块接口：

```text
answer(question, context) -> AnswerResult
```

其中 `context` 包含 project_id、scope、as_of_commit/time、token_budget、是否包含
proposal 和当前 session；调用者不需要知道 intent router、source policy、ranker、
evidence resolver 或 answer composer 的内部顺序。

CLI、MCP、WorkflowBrain 和未来 UI 都通过 Adapter 使用该接口。原有 `brain.ask`
继续作为兼容 Adapter。

### D6-002：检索和回答分离

检索负责找到候选，AnswerBrain 负责判断哪些候选可以进入回答：

```text
question
  -> intent classification
  -> source policy
  -> candidate retrieval
  -> relevance gate
  -> fact aggregation
  -> evidence/provenance check
  -> answer composition
```

候选数量不等于最终 facts 数量。答案可以只引用 1 条高质量 Memory，即使底层检索
找到了 10 条候选。

### D6-003：默认回答事实，不默认回答历史

默认只返回当前有效的 verified/active facts。历史版本、stale facts、proposal、
Event 和相关方案放入独立字段，只有问题明确要求历史或调用者显式开启时才进入答案。

### D6-004：断言级 provenance

答案不是只在整体层面附 source_ids，而是每个 key point 都带来源：

```json
{
  "text": "WorkflowBrain 的外部接口是 start_session、observe、end_session。",
  "source_ids": ["K-021"],
  "evidence_ids": ["E-012"],
  "support": "direct"
}
```

## 4. 问题意图模型

### 4.1 Intent 类型

第一版支持有限、可解释的意图集合：

```text
project_goal
current_state
feature_summary
mechanism_explanation
decision_reason
failure_experience
evidence_trace
version_history
task_next_step
test_result
file_or_module_lookup
generic_search
```

Intent 识别优先使用规则和关键词；模型只作为可选 fallback。无法确定时使用
`generic_search`，不伪装成高确定性意图。

### 4.2 Intent Policy

每种 intent 映射：

- 首选 memory types；
- 可接受的 status；
- 是否包含历史；
- 是否优先 Evidence；
- 默认答案长度；
- 推荐的后续动作。

例如：

| Intent | 首选来源 | 默认排除 |
|---|---|---|
| project_goal | identity/state/decision | 普通 task、Event |
| feature_summary | knowledge/decision/project model | 旧 handover、低分 Event |
| mechanism_explanation | knowledge/decision/evidence | 无关版本记录 |
| decision_reason | decision/experience/evidence | 普通目录说明 |
| failure_experience | experience/evidence | 未验证 proposal |
| evidence_trace | evidence/links/events | 无来源摘要 |
| current_state | state/task/handover | 旧版本规划 |

## 5. 来源质量和知识污染

### 5.1 Source Class

Memory 和 Evidence 增加或推导来源分类：

```text
user_confirmed
verified_evidence
active_decision
active_knowledge
project_model
task_handover
event_observation
proposal
generated_summary
documentation_example
```

来源分类影响排序和 confidence，但不能绕过 project_id、有效期和 evidence 规则。

### 5.2 污染检测

对 ingestion 和 answer candidate 增加以下标记：

- `code_block`：来自 Markdown fenced code 或源码片段；
- `example_only`：示例 JSON、伪代码和文档模板；
- `version_plan`：方案规划内容，不代表当前实现；
- `test_summary`：测试总结，可能重复引用底层证据；
- `current_state`：当前状态；
- `historical`：历史版本或已 supersede 内容。

`documentation_example` 默认不进入 facts；`version_plan` 只有问题询问版本规划时才
进入正文；`test_summary` 必须尽量指向原始测试 Evidence，不能重复计算证据数量。

### 5.3 当前性规则

当以下内容发生冲突时，优先级为：

```text
当前已验证 Evidence
  > 当前 active Decision/Knowledge
  > 最近 Project Model
  > 最近 Task/Handover
  > 历史文档和 generated summary
```

旧版本内容仍可通过 `as_of_commit` 或 `as_of_time` 查询，但不能默认污染当前答案。

## 6. AnswerResult

统一回答结构：

```json
{
  "schema_version": "0.6",
  "answer": "一句直接回答",
  "key_points": [
    {
      "text": "核心事实",
      "source_ids": ["D-001"],
      "evidence_ids": ["E-001"],
      "support": "direct"
    }
  ],
  "facts": [],
  "evidence": [],
  "related_context": [],
  "uncertainties": [],
  "next_action": null,
  "intent": "mechanism_explanation",
  "match_mode": "fts",
  "confidence": 0.88,
  "confidence_breakdown": {},
  "provenance": []
}
```

默认 Agent 只需要读取 `answer` 和 `key_points`。详细 facts、evidence、related_context
和 provenance 用于追问、审计和调试。

## 7. Confidence 模型

### 7.1 分项计算

confidence 不再只依赖命中数量，至少拆为：

```text
retrieval_confidence  检索相关度
source_confidence     来源权威性
evidence_coverage     答案被证据支持的比例
freshness             当前性和时效
conflict_penalty      冲突惩罚
```

最终值必须受以下规则约束：

- 没有任何可靠候选：`0.0`；
- 有事实但无 Evidence：不得因为命中数量而给出最高置信度；
- 存在未解决冲突：降低 confidence，并加入 uncertainty；
- 只有 proposal：facts confidence 为 `0.0`，proposal 单独展示；
- stale fact 不得提升当前回答 confidence。

### 7.2 Evidence Coverage

以 key point 为分母，而不是以搜索结果数量为分母：

```text
evidence_coverage = 有直接证据的 key_points / key_points 总数
```

如果回答包含“26 测试全绿”，但只有一条非直接测试总结作为来源，则应明确标注
`coverage=partial`，而不是输出无条件高置信度。

## 8. 回答策略

### 8.1 直接回答

答案正文只允许使用：

- 与 intent 匹配的字段；
- 通过 relevance gate 的事实；
- 已确认且当前有效的来源；
- 有明确 provenance 的聚合结论。

禁止将完整 JSON、整段 Markdown、目录树和代码文件名直接作为 answer 内容，除非问题
明确要求源码或文件定位。

### 8.2 相关上下文

以下信息放到 `related_context`：

- 历史版本；
- 相邻但非核心的 Decision；
- 相关 Task/Handover；
- stale fact；
- pending proposal；
- 可能有帮助但未直接支持答案的记录。

### 8.3 无匹配和低质量匹配

当没有达到阈值的候选时：

```json
{
  "answer": "未找到足够相关的已确认记录。",
  "facts": [],
  "related_context": [],
  "uncertainties": ["可以尝试更具体的关键词，或先记录该事实。"],
  "confidence": 0.0
}
```

不得为了满足 limit 而填充事实。

## 9. Agent 体验

### 9.1 分层输出

WorkflowBrain 的启动上下文默认采用 AnswerBrain 的 compact 模式：

```text
当前目标
当前阻塞
下一步
一条关键风险
pending/stale 数量
source IDs
```

Agent 追问时再返回 focused answer。完整证据链和 related context 只在需要时展开。

### 9.2 后续动作

当证据不足或信息过期时，`next_action` 返回可执行动作：

```json
{
  "type": "run_test",
  "command": "make test",
  "target_ids": ["D-003"],
  "reason": "当前决策缺少最新 commit 下的测试证据"
}
```

动作只是建议，不由 AnswerBrain 自动执行。

## 10. 兼容和降级

- `brain.ask` 保持现有请求参数和主要返回字段；
- 新增 `answer_v2` 或通过 `schema_version=0.6` 暴露增强结构；
- 旧 Agent 只读取 `answer/facts/evidence/uncertainties/confidence` 时仍能工作；
- AnswerBrain 异常时回退到 v0.5 ranked search + 旧 answer builder；
- 没有 intent classifier 时使用 generic_search；
- 没有 evidence resolver 时返回已有 source IDs，并降低 evidence coverage。

## 11. 实施阶段

### Phase 1：回答模型和来源分类

- 定义 AnswerResult、KeyPoint、ConfidenceBreakdown；
- 增加 source class 和污染标记；
- 将现有 ask answer builder 收进 AnswerBrain implementation；
- 保持旧协议 Adapter 不变。

### Phase 2：Intent Router 和 Source Policy

- 实现规则 intent classifier；
- 建立 intent 到 type/status/source 的 policy；
- 增加当前事实与历史内容分层；
- 增加 intent fixture 和错误分类回退测试。

### Phase 3：聚合、证据覆盖和回答呈现

- 实现 relevance gate 后的 fact aggregation；
- 增加断言级 provenance；
- 实现 related_context、stale_fact、proposal 分层；
- 实现 confidence breakdown 和 evidence coverage。

### Phase 4：污染治理和 Agent 体验

- ingestion 标记代码块、示例、版本规划和测试总结；
- compact/focused answer；
- next_action 建议；
- 默认输出去 JSON 噪声和低相关上下文。

### Phase 5：回放评测和真实 Agent 试用

- 建立真实问题集和期望答案边界；
- 对比 v0.5 与 v0.6 的 precision、噪声率和证据覆盖；
- 在个人 Agent 连续 session 中试用；
- 只有通过人工检查后，才调整默认 source policy 和阈值。

## 12. 测试与验收

### 12.1 必须通过的场景

1. “项目核心目标”只返回 Identity/State 核心内容，不混入普通任务和版本历史。
2. “v0.4 包含哪些功能”返回聚合后的核心点，历史版本进入 related_context。
3. “WorkflowBrain 核心功能”不把模块源码文件名当作主要事实。
4. “证据路径如何支持迁移”优先返回 Evidence locator 和解析顺序。
5. “并发控制如何解决冲突”返回 revision/Conflict 机制，并准确显示证据覆盖。
6. 代码块、文档示例和方案规划默认不会进入当前 facts。
7. 测试总结与原始测试证据重复时，只保留一组主要事实并建立 provenance。
8. 只有 proposal 时默认 facts 为空，显式开启才显示 proposal。
9. stale、冲突、无证据和历史版本都会影响 confidence breakdown。
10. 无关问题返回 0 facts、confidence 0 和明确 uncertainty。
11. 同一问题在其他项目增加大量数据后，答案和 source_ids 不变。
12. AnswerBrain、intent classifier 或 evidence resolver 失败时均能安全降级。
13. v0.5 WorkflowBrain 的 start/observe/end 和 v0.4/v0.5 协议继续兼容。
14. compact/focused/full 三种输出在 token budget 下保持确定性。

### 12.2 质量指标

在固定查询集上记录：

- answer precision：答案核心断言正确且相关的比例；
- context noise rate：无助于回答的内容占比；
- evidence coverage：关键断言被直接证据支持的比例；
- provenance completeness：断言具有 source ID 的比例；
- intent accuracy：意图分类准确率；
- stale/conflict disclosure rate：过期或冲突被正确披露的比例；
- fallback availability：AnswerBrain 故障时旧路径成功率。

质量底线：

- 无关问题误返回 facts：0；
- 无来源断言进入高置信度答案：0；
- proposal 混入默认事实：0；
- 跨项目污染：0；
- 旧协议回归通过率：100%。

## 13. 最终判断

v0.6 的完成标准不是“返回更多内容”，而是：

> **Brain 能理解 Agent 在问什么，只用最相关、最权威、当前有效且有 provenance 的事实回答，并把历史、建议、冲突和不确定性放在正确的位置。**

完成这一版后，Project Brain 才从“可检索的项目记忆”真正进入“面向 Agent 的可信项目问答层”。
