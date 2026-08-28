# Project Brain

> 换 Agent，不换脑。项目级认知层，让项目持续拥有身份、状态、知识、经验、决策与证据。

- 规格：`project-brain-v0.1.md`（v0.1 MVP 工程方案）
- 第二版方案：`project-brain-v0.2.md`（可靠记忆与项目隔离）
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

- `brain.onboard` — 新 Agent 获取项目交接包（含 latest_handover、blocked/active task、missing_context）
- `brain.ask` — 自然语言查询，返回 `match_mode/matches/answer/facts/evidence/confidence`，阈值过滤低相关
- `brain.record` — 写入知识/经验/决策/任务/证据/事件（含 `task_status`、evidence 校验、duplicate/conflicts）
- `brain.handover` — 生成并持久化交接报告（原子更新 task_status 与 state，task 不存在则报错）
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

## 已知限制（v0.2）

- 单库多项目（`project_id` 隔离），SQLite 单文件，无并发控制
- 检索仅 FTS5 + 中文 bigram 回退 + 阈值，不引入向量库
- Curator 为规则引擎 + `SearchProvider` 抽象（为混合检索预留）

## 验收

- [x] v0.1 全部能力 + 多项目隔离（同一 DB 跨项目污染率为 0）
- [x] 8 类实体 + 任务状态分离（`task_status: draft/in_progress/blocked/completed/cancelled`）
- [x] `brain.ask` 阈值、`match_mode/matched_terms`、scope 过滤、`量子纠缠` 等无关查询 0 facts
- [x] `brain.handover` 原子更新 + 任务不存在报错 + `latest_handover` 回显 + `missing_context`
- [x] 证据双向追溯（`evidence_of`）+ `verify/invalidate` + `duplicate_of/conflicts_with`
- [x] 可重复 Demo：`scripts/brain-demo.py`（临时隔离库，验证隔离/阈值/scope/状态流转）
- [x] 重启可恢复，`schema_meta` 版本化，旧库迁移自 `config.json` 回填 `project_id`

见 `project-brain-v0.1.md` §14 与 `project-brain-v0.2.md` §9。
