# Project Brain - MCP Server 全局安装指南

## 快速开始

### 1. 测试 MCP Server

```bash
cd "/Users/fenghui/Desktop/Project Brain"
PYTHONPATH=brain-server/src python3 -m brain_server.server
```

### 2. 配置到 Claude Desktop

编辑 `~/.claude/mcp-config.json`：

```json
{
  "mcpServers": {
    "project-brain": {
      "command": "python3",
      "args": ["/Users/fenghui/Desktop/Project Brain/brain-server/bin/brain-mcp"]
    }
  }
}
```

### 3. 配置到 Cursor

在 Cursor 设置中搜索 "MCP"，添加：
- Name: `project-brain`
- Command: `python3`
- Args: `/Users/fenghui/Desktop/Project Brain/brain-server/bin/brain-mcp`

### 4. 初始化数据库

```bash
python3 scripts/init-brain.py --project your-project-name
```

## 可用工具

| 工具 | 用途 |
|---|---|
| `brain_onboard` | 获取项目上下文 |
| `brain_ask` | 查询项目知识 |
| `brain_record` | 记录知识/决策/任务 |
| `brain_handover` | 会话交接 |
| `brain_verify` | 验证记忆 |
| `brain_link` | 建立关联 |
| `brain_ingest` | 自动采集事件 |
| `brain_curate` | 自动提炼知识 |

## 数据位置

- 数据库：`.brain/brain.db`
- 配置：`.brain/config.json`
- 交接报告：`.brain/exports/latest-handover.md`
