# Project Brain / v0.2 可靠记忆方案

> 版本：v0.2
>
> 状态：第二版实施方案
>
> 核心目标：**先保证记忆属于正确的项目，再扩大记忆的覆盖和理解能力。**

## 1. v0.1 复盘

v0.1 已经证明了核心价值：新 Agent 可以通过 `brain.onboard`、`brain.ask` 和
`brain.handover` 继承项目身份、当前状态、关键决策、失败经验和下一步工作。

当前仓库的实际验证结果是：

- `make test`：2/2 通过。
- SQLite 持久化、FTS5 检索、规则 Curator 和 Markdown handover 已可运行。
- `make demo` 当前失败：数据库配置为 `project-brain`，demo 请求使用
  `smart-gateway`，但 `project_id` 尚未参与存储和查询过滤。
- 无匹配问题已经可以返回 0 条 facts，但相关问题仍可能混入低相关记录或完整 JSON。
- handover 会把任务写成 `in_progress`，而状态定义和 onboarding 过滤尚未统一。

因此，v0.2 不把“引入向量库”作为第一目标。第一目标是让 Brain 的结果可信、隔离、
可复现。

## 2. 目标与非目标

### 2.1 目标

1. 同一个数据库可以安全承载多个项目，任何读写都不会跨项目污染。
2. 任务、记忆、交接的状态定义统一，handover 后下一个 Agent 能看到正确任务。
3. ask 能区分可靠命中、弱命中和无匹配，不再用低相关内容凑答案。
4. 重要记忆、证据、事件和 handover 之间形成完整可追溯链路。
5. 用独立项目场景、空库场景、重启场景和负面查询场景完成可重复验收。
6. 为后续混合检索预留接口，但核心功能不依赖 embedding 或外部模型。

### 2.2 非目标

- v0.2 不做团队权限、远程同步和云端部署。
- 不自动把 Agent 输出升级为 `verified`。
- 不引入独立向量数据库。
- 不在本版实现复杂的多 Agent 任务调度和租约。
- 不复制项目文件内容；证据仍以路径、commit、日志和测试引用为主。

## 3. 核心设计决定

### D2-001：所有实体必须带 `project_id`

`project_id` 不再只是协议参数，而是数据模型和查询边界的一部分。以下表都必须带
`project_id`：`memories`、`evidence`、`events`、`handovers`、`links`。

所有 repository 方法都接收项目 ID；协议层不得调用没有项目过滤的列表、搜索或关联方法。

### D2-002：状态分为记忆状态和任务状态

记忆状态继续使用：

```text
draft | proposed | observed | verified | active | deprecated | invalid
```

任务状态单独使用：

```text
draft -> in_progress -> blocked -> completed
                         \-> cancelled
```

`in_progress`、`blocked`、`completed` 不再写入记忆状态字段。Task 的任务状态放在
Task 内容或单独的 `task_status` 字段中，避免 onboarding 把任务误判为普通记忆。

### D2-003：相关性优先于结果数量

`brain.ask` 可以返回少于 `limit` 的结果。若所有候选结果都低于阈值，必须返回空
`facts` 和明确的不确定性，而不是为了填满列表而返回项目状态或无关记录。

### D2-004：证据链必须可双向追溯

查询记忆时可以找到证据，查询证据时也可以反查被支持的记忆。保留统一关系语义：

```text
memory --evidence_of--> evidence
memory --supports------> memory
memory --supersedes----> memory
memory --conflicts_with> memory
```

## 4. 数据模型和迁移

### 4.1 Schema 变化

```sql
ALTER TABLE memories  ADD COLUMN project_id TEXT NOT NULL DEFAULT 'default';
ALTER TABLE evidence  ADD COLUMN project_id TEXT NOT NULL DEFAULT 'default';
ALTER TABLE events    ADD COLUMN project_id TEXT NOT NULL DEFAULT 'default';
ALTER TABLE handovers ADD COLUMN project_id TEXT NOT NULL DEFAULT 'default';
ALTER TABLE links     ADD COLUMN project_id TEXT NOT NULL DEFAULT 'default';

CREATE INDEX idx_memories_project_type_status
  ON memories(project_id, type, status);
CREATE INDEX idx_evidence_project_source
  ON evidence(project_id, source);
CREATE INDEX idx_events_project_created
  ON events(project_id, created_at);
CREATE INDEX idx_handovers_project_created
  ON handovers(project_id, created_at);
```

实际实现应使用 schema version 和迁移函数，不直接依赖重复执行 `ALTER TABLE`。旧库
迁移时从 `.brain/config.json` 补齐已有记录的项目 ID；无法确定来源的旧记录标为
`migration-unassigned`，不得静默归入当前项目。

### 4.2 统一记录元数据

新增或明确以下字段：

- `project_id`
- `source_event_ids`
- `evidence_ids`
- `scope` 或 tags
- `branch`、`commit`（有 Git 证据时）
- `valid_from`、`valid_until`（需要表达时效时）

v0.2 仍可将部分字段保存在 JSON content 中，但协议返回必须提供稳定的顶层引用。

## 5. 协议调整

### 5.1 `brain.onboard`

返回内容调整为：

- 项目身份和当前状态
- `in_progress`、`blocked` 的任务
- active/verified decision
- verified failure experience
- 最近一次 handover
- open questions、blockers 和下一步
- 每个摘要项对应的 `source_ids` 和 `evidence_ids`

加入 `missing_context`，明确哪些字段尚未设置。`token_budget` 采用确定性裁剪，
优先级固定为：State → blocked task → active task → Decision → Failure → 最近 handover。

### 5.2 `brain.ask`

返回结果增加：

```json
{
  "match_mode": "exact|fts|like_fallback|none",
  "matches": [
    {"id": "D-001", "score": 0.91, "matched_terms": ["向量库"]}
  ],
  "facts": [],
  "evidence": [],
  "uncertainties": [],
  "confidence": 0.0
}
```

实现顺序：

1. FTS5 查询；
2. 中文 bigram/LIKE fallback；
3. 归一化分数、状态权重和 scope 过滤；
4. 应用最低相关度阈值；
5. 只从命中的结构化字段生成简短答案。

无匹配时不返回 facts。低相关结果只能放入 `uncertainties` 或 `suggestions`，不能
当作事实回答。

### 5.3 `brain.record`

增加：

- 项目边界校验；
- type-specific 必填字段校验；
- evidence 引用 ID 校验；
- 明确的 duplicate 结果：`duplicate_of`、`similarity`、`action`；
- 冲突记录不覆盖旧记录，自动创建 `conflicts_with` 或 `supersedes`。

重复记录继续接受但不覆盖，并返回告警；证据引用不存在时整条记录不得升级为
`verified`。

### 5.4 `brain.handover`

handover 必须原子更新：

1. 写入 handover；
2. 更新 Task 的任务状态、completed、remaining、next_step；
3. 更新 State 的 recent_changes、open_questions 和 blockers；
4. 写入 handover Event；
5. 生成 latest Markdown；
6. 返回新的状态和全部引用。

如果 task_id 不存在，应返回明确错误，不得只生成一份看似成功的 handover。

## 6. 检索架构

### v0.2：可靠的词法检索

FTS5 和中文 fallback 继续保留，但把搜索拆为三个阶段：

```text
query parser -> candidate retrieval -> relevance gate -> answer builder
```

候选排序至少综合：

- FTS rank 或 LIKE 命中强度；
- query 与 content/tags 的命中比例；
- status 权重；
- evidence 是否存在；
- 更新时间；
- focus/scope 是否匹配。

### v0.2.1：可选混合检索接口

定义 `SearchProvider` 接口：

```text
search(project_id, query, scope, limit) -> SearchResult[]
```

默认实现为 FTS5；未来可增加 embedding provider。没有配置 embedding 时，系统行为
和结果格式不变。

## 7. 事件和证据自动采集

v0.2 先做低风险、可复核的采集：

- `brain status` 记录关键统计；
- CLI 提供 `brain event` 或在 record/handover 中统一写 Event；
- 允许从 Git 当前 commit、变更文件和测试命令显式传入 Evidence；
- 不自动读取全部 diff，也不把全部终端输出塞进长期记忆。

自动采集的每条事件必须包含 agent、session、project、时间和来源；采集失败不影响
主任务，但要在响应中给出 warning。

## 8. 实施顺序

### Phase 1：数据边界和迁移

- 增加 schema version 与迁移机制；
- 全表加入 `project_id`；
- repository 全面改为显式项目过滤；
- 修复 init、CLI、MCP 和 demo 的项目 ID 一致性；
- 增加双项目隔离测试。

### Phase 2：状态和交接可靠性

- 分离 memory status 与 task status；
- 修复 handover 的原子更新和不存在 task 错误；
- onboard 读取最近 handover、blocked/in-progress task；
- 增加 handover 后重启恢复测试。

### Phase 3：检索可信度

- 统一 FTS、LIKE fallback 和排序分数；
- 增加相关度阈值、match_mode 和 matched_terms；
- 修复答案 builder 的字段选择；
- 增加无关问题、中文改写和 scope 过滤测试。

### Phase 4：证据、冲突和可维护性

- 完善证据双向查询；
- 增加 verify/invalidate；
- 增加冲突与 supersedes 关系；
- 增加 export/import、备份和 evidence health 检查。

### Phase 5：可选混合检索

- 只实现 SearchProvider 抽象和离线接口；
- 用固定评测集比较 FTS 与 embedding；
- 只有当语义召回有明确收益时，才接入本地 embedding。

## 9. 测试和验收

### 9.1 必须通过的场景

1. 初始化两个项目，项目 A 的 ask/onboard 看不到项目 B 的任何记录。
2. 项目 ID 不匹配时，写入、查询和 handover 都得到明确错误或空结果。
3. Agent A handover 后，Agent B 能看到同一个任务的 remaining、next_step 和状态。
4. task 完成后不再出现在 active task，但仍可通过历史或 source ID 追溯。
5. `量子纠缠` 等无关问题返回 0 facts、低 confidence 和明确 uncertainty。
6. 中文长句 fallback 能命中相关记录，改写问题不会返回整段无关 state JSON。
7. 无 evidence 的 decision 仍为 proposed；不存在的 evidence ID 不得伪装为 verified。
8. duplicate、conflict、supersedes 都保留原记录且可查询关系。
9. 重启数据库后，memory、evidence、link、event、handover 关系不丢失。
10. demo 从干净临时目录开始运行，重复运行结果稳定。

### 9.2 质量指标

v0.2 不虚构线上准确率，先建立可重复的离线评测集：

- 10 个精确问题；
- 10 个中文改写问题；
- 10 个无关问题；
- 5 个 scope 过滤问题；
- 5 个跨项目污染问题。

记录 `hit@1`、`hit@5`、无关问题误命中率和 evidence 覆盖率。第一版目标是：跨项目
污染率为 0，无关问题误返回 facts 为 0，关键交接场景全部通过。

## 10. 交付物

- `project-brain-v0.2.md`：本方案；
- schema migration 和版本化数据库；
- 更新后的四个核心协议；
- 双项目、状态机、检索阈值和证据链测试；
- 可从空目录重复运行的 v0.2 demo；
- 更新后的 README、限制说明和迁移说明；
- 一份 v0.2 验收报告，包含测试命令和结果。

## 11. 最终判断

v0.2 的完成标准不是“能搜到更多”，而是：

> **换 Agent、换会话、换项目上下文之后，Brain 仍只提供正确项目的、相关的、可追溯的事实，并让任务状态可以可靠接续。**

向量检索、Curator 小模型和团队协作能力都保留在演进接口之后，等核心可信度通过验收
再进入下一版。
