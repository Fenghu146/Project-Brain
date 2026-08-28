# Project Brain

> 换 Agent，不换脑。项目级认知层，让项目持续拥有身份、状态、知识、经验、决策与证据。

- 规格：`project-brain-v0.1.md`（v0.1 MVP 工程方案）
- 第二版方案：`project-brain-v0.2.md`（可靠记忆与项目隔离）
- 第三版方案：`project-brain-v0.3.md`（主动认知与可审阅 Curator）
- 计划：`.commandcode/plans/project-brain-v01-prototype-plan.md`

## 目录结构

```
Project Brain/
├── .brain/                 # 运行时数据（brain.db 不提交）
│   ├── brain.db
│   ├── config.json
│   └── exports/latest-handover.md
├── brain-server/           # MCP Server 与 Brain Core
│   ├── src/
│   └── tests/
├── scripts/
│   ├── init-brain.py
│   └── brain-demo.py
├── docs/
└── project-brain-v0.1.md
```

## 快速开始

```bash
make init   # 初始化 Brain 数据库与种子数据
make test   # 运行测试
make demo   # 端到端 Agent A → B 交接演示
```

## 协议

- `brain.onboard` — 新 Agent 获取项目交接包（含 latest_handover、blocked/active task、missing_context、`pending_reviews`/`stale_context`/`project_model_summary`/`basis_commit`）
- `brain.ask` — 自然语言查询，返回 `match_mode/matches/answer/facts/evidence/confidence`，含 `include_proposals/as_of_commit/time`、`stale_fact/verification_suggestion/provenance`
- `brain.record` — 写入知识/经验/决策/任务/证据/事件（含 `task_status`、`origin`、`valid_*`、`branch/commit`、evidence 校验、duplicate/conflicts）
- `brain.handover` — 生成并持久化交接报告（原子更新 task_status 与 state，附 `session_event_ids/pending_proposals/verification_suggestions/basis_commit/snapshot`）
- `brain.ingest` / `brain.curate` / `brain.review_list` / `brain.review_apply` / `brain.snapshot` — 事件采集、提案审阅与模型快照
- `brain.verify` / `brain.link` / `brain.export` — 校验、关系与导出

## CLI（跨项目）

```bash
brain init --dir /path/to/proj --project my-proj --seed
brain status --project my-proj
brain onboard --agent agent-b --focus "任务名"
brain ask --agent agent-b --question "为什么用 FTS5" --scope search
brain record --agent agent-b --type knowledge --content '{"content":"..."}'
brain verify --id D-001 --action verify
brain link --from-id D-001 --relation supports --to-id K-001
brain handover --agent agent-b --task T-001 --status partial --completed '["..."]'
brain export --project my-proj --what links
```

## 已知限制（v0.3）

- 单库多项目（`project_id` 隔离），SQLite 单文件，无并发控制
- 检索仅 FTS5 + 中文 bigram 回退 + 阈值 + `EmbeddingProvider` 预留（异常/低分不影响 FTS）
- Curator 为规则引擎为主、`ModelCuratorAdapter` 可插拔（默认关闭，失败自动回退）
- Project Model 为派生视图 + 快照，不引入图数据库

## 验收

- [x] v0.1 + v0.2 全部能力保留（隔离/阈值/事务/证据链/可重复 demo）
- [x] Event Ingestion（git/test/file，显式触发，`dedup_key` 幂等，跨项目隔离）
- [x] Proposal 与 Review Queue（`pending→approved/rejected/deferred/superseded`，目标版本变化 `superseded`，四状态均写 Event）
- [x] `ask` 时效与过期（`as_of_commit/time`、`valid_*`、`stale_fact` 降级 + `verification_suggestion`）
- [x] Project Model + `model_snapshots`（7 层视图，删后重建 `source_ids` 一致，`provenance` 可追溯）
- [x] `onboard` 增强（`pending_reviews/stale_context/verification_suggestions/project_model_summary/basis_commit`，pending 不混入 `important_decisions`）
- [x] 可选 ModelCurator/Embedding 预留（关闭或异常时不影响核心，自动回退 Rule）
- [x] 18 tests + 回放 CLI/MCP 全绿（`brain ingest/curate/review/snapshot/ask --include-proposals`）

见 `project-brain-v0.1.md` §14、`project-brain-v0.2.md` §9、`project-brain-v0.3.md` §10。
