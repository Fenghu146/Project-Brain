# Project Brain 自主托管指南

## 核心设计

Brain 采用**每项目独立数据库**的设计，每个项目拥有自己的 `.brain/` 文件夹：

```
your-project/
├── .brain/
│   ├── brain.db           # SQLite 数据库（可提交到 git）
│   ├── config.json        # 项目配置
│   └── exports/           # 交接报告等导出文件
├── src/
├── tests/
└── README.md
```

## 为什么这样设计

| 特性 | 说明 |
|---|---|
| **可移植** | `.brain/` 随项目一起迁移，新环境自动可用 |
| **可提交** | 可选择提交到 git，项目知识随代码版本管理 |
| **隔离** | 不同项目数据完全独立，无跨项目污染 |
| **自包含** | 不需要全局配置，任何机器 clone 后即可用 |

## 快速开始

### 1. 初始化项目

```bash
# 在项目目录中
brain init --project my-project --seed
```

这会创建 `.brain/` 文件夹并初始化数据库。

### 2. 配置 MCP Server

在 `~/.claude/mcp-config.json` 中添加：

```json
{
  "mcpServers": {
    "project-brain": {
      "command": "python3",
      "args": [
        "/Users/fenghui/Desktop/Project Brain/brain-server/bin/brain-mcp"
      ],
      "env": {
        "BRAIN_DB_PATH": "/absolute/path/to/your/project/.brain/brain.db"
      }
    }
  }
}
```

或者使用相对路径（从项目目录启动时）：

```json
{
  "mcpServers": {
    "project-brain": {
      "command": "python3",
      "args": ["./brain-server/bin/brain-mcp"]
    }
  }
}
```

### 3. 使用

进入项目目录后，所有 `brain` 命令自动使用 `.brain/brain.db`：

```bash
cd /path/to/your-project

# Onboard 新 Agent
brain onboard --agent agent-b --focus "当前任务"

# 查询知识
brain ask --question "项目核心目标是什么"

# 记录知识
brain record --type knowledge --content '{"content":"重要发现..."}'

# 会话交接
brain handover --task T-001 --status completed --completed '["完成xxx"]'
```

## 数据位置选择

### 方案 A：提交到 Git（推荐用于关键项目）

将 `.brain/` 加入版本控制，项目知识随代码一起演进：

```bash
# 从 .gitignore 中移除 .brain/
sed -i '' '/\.brain\//d' .gitignore

# 提交
git add .brain/
git commit -m "chore: add brain database"
```

### 方案 B：仅本地存储（推荐用于敏感项目）

保持 `.brain/` 在 `.gitignore` 中，仅在本地使用：

```
# .gitignore
.brain/
```

### 方案 C：混合模式

提交 `config.json` 和 schema，数据库本地生成：

```json
// .brain/config.json (可提交)
{
  "project_id": "my-project",
  "created_at": "2026-08-29T...",
  "version": "0.1.0"
}
```

## CLI 参考

```bash
# 初始化
brain init --project <name> [--dir <path>] [--seed]

# Onboard
brain onboard --agent <id> [--focus <query>]

# Ask
brain ask --question "<query>" [--scope <types>]

# Record
brain record --type <kind> --content '<json>'

# Handover
brain handover --task <id> --status <completed|partial|failed>

# Doctor
brain doctor --detail

# Feedback
brain feedback --question "<q>" --verdict <accepted|corrected|irrelevant>
```

## 多项目切换

当有多个项目时，指定数据库路径：

```bash
brain --db /path/to/project-a/.brain/brain.db ask --question "..."
brain --db /path/to/project-b/.brain/brain.db record --type knowledge ...
```

或在项目目录中使用别名：

```bash
alias brain-a='brain --db ~/projects/project-a/.brain/brain.db'
alias brain-b='brain --db ~/projects/project-b/.brain/brain.db'
```

## 与中心化模式对比

| 特性 | 自主托管（推荐） | 中心化 |
|---|---|---|
| 数据位置 | 项目 `.brain/` 内 | 全局统一位置 |
| Git 集成 | 可选提交 | 不相关 |
| 移植性 | 高（clone 即用） | 需配置 |
| 备份 | 随项目备份 | 单独备份 |
| 适用场景 | 大多数项目 | 团队共享知识库 |

## 最佳实践

1. **关键项目提交 `.brain/`** - 让项目知识随代码版本管理
2. **敏感项目忽略 `.brain/`** - 避免机密数据入库
3. **定期导出** - 使用 `brain export` 备份关键数据
4. **Doctor 检查** - 定期运行 `brain doctor --detail` 维护健康
