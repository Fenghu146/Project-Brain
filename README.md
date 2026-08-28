# Project Brain

> 换 Agent，不换脑。项目级认知层，让项目持续拥有身份、状态、知识、经验、决策与证据。

- 规格：`project-brain-v0.1.md`（v0.1 MVP 工程方案）
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

- `brain.onboard` — 新 Agent 获取项目交接包
- `brain.ask` — 自然语言查询，返回答案与证据
- `brain.record` — 写入知识/经验/决策/任务/证据/事件
- `brain.handover` — 生成并持久化交接报告

## 已知限制（v0.1）

- 单机单项目，SQLite 单文件，无并发控制
- 检索仅 FTS5，无向量/语义搜索
- Curator 为规则引擎，无 LLM

## 验收

见 `project-brain-v0.1.md` §14。
