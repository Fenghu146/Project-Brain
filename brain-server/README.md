# brain-server

Project Brain MCP Server — SQLite + FTS5 + 4 core protocols.

## Run

```bash
# init
python scripts/init-brain.py

# test
PYTHONPATH=brain-server/src python -m pytest brain-server/tests -v

# demo (Agent A -> Brain -> Agent B)
PYTHONPATH=brain-server/src python scripts/brain-demo.py

# MCP server (stdio)
PYTHONPATH=brain-server/src python -m brain_server.server
```

## MCP tools

- `brain_onboard(project_id, agent_id, session_id?, focus?, token_budget?)`
- `brain_ask(project_id, agent_id, question, session_id?, scope?, include_evidence?, limit?)`
- `brain_record(project_id, agent_id, records, session_id?)`
- `brain_handover(project_id, agent_id, status, session_id?, task_id?, completed?, failed?, discovered?, remaining?, recommended_next_step?, evidence_ids?)`

## Storage

- `.brain/brain.db` — SQLite (gitignored), FTS5 via `memory_fts`
- `.brain/config.json` — project_id + version
- `.brain/exports/latest-handover.md` — last handover markdown
