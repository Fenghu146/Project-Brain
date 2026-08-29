# Project Brain / v0.4 可运营方案

> 版本：v0.4
>
> 状态：第四版实施方案
>
> 核心目标：**让已经可信的 Brain 能在真实项目、真实机器和多个并行 Agent 中长期运行。**

## 1. 版本定位

v0.3 已经完成了主动认知闭环：事件采集、幂等、Rule/Model Curator、Proposal 审阅、
时效与版本过滤、Project Model 快照和可插拔搜索均已验证。

下一阶段的主要风险不再是“Brain 会不会主动提出建议”，而是：

- 数据量和项目数量增加后，检索是否仍然正确且足够快？
- Brain 从一台机器迁移到另一台机器后，证据路径是否仍然有效？
- 多个 Agent 同时写入时，proposal、review 和 handover 是否会互相覆盖？
- CLI、MCP 和数据库升级后，旧客户端是否还能稳定工作？
- 出错、损坏、误审阅后，用户能否诊断、备份、恢复和审计？

v0.4 目标是“可运营化”，而不是继续扩大自动推理范围。自动认知仍遵守 v0.3 的
proposal 边界：任何推断不能绕过审阅直接成为事实。

## 2. 目标与非目标

### 2.1 目标

1. 让 FTS 检索具备项目级索引边界和可预测的性能退化行为。
2. 让 Evidence 路径、Git 仓库和项目目录在迁移后可重新定位。
3. 支持多个 Agent 并行 ingest、curate、review 和 handover，避免丢写和静默覆盖。
4. 建立 API/schema 兼容策略，CLI 参数在全局和子命令位置都不易踩坑。
5. 提供 `doctor`、备份、恢复、迁移预览和审计能力。
6. 支持本地多项目长期运行，并为未来服务化部署保留边界清晰的接口。

### 2.2 非目标

- 不在 v0.4 引入远程同步协议或云端多租户服务。
- 不引入权限系统作为核心功能；本版只做本地安全边界和清晰的安全警告。
- 不让并发能力演变成完整的多 Agent 编排器。
- 不新增自动批准模型建议的路径。
- 不为了性能复制全部项目文件或测试日志。

## 3. 核心设计决定

### D4-001：项目根目录是运行时身份，绝对路径不是身份

Brain 配置保存：

```json
{
  "project_id": "project-brain",
  "project_root": ".",
  "brain_version": "0.4",
  "path_policy": "relative_preferred"
}
```

Evidence 优先保存相对于 `project_root` 的路径，并保留采集时的绝对路径作为诊断
信息而非唯一定位。迁移后按以下顺序解析：

1. 当前配置的 `project_root`；
2. 数据库所在项目根目录；
3. 用户显式提供的 `--project-root`；
4. 旧绝对路径仅用于提示，不作为默认访问路径。

### D4-002：正确性优先于索引捷径

项目过滤必须在搜索结果形成之前完成，不能只取少量全局 FTS 结果后再过滤。v0.4
采用带项目分区字段的 FTS 查询或按项目维护的 FTS 分区；迁移期间通过固定评测集
证明结果集合不会因其他项目数量增加而变化。

### D4-003：写入采用短事务和乐观版本校验

每个可更新实体增加 `revision`。更新时可以提供 `expected_revision`：

- 版本一致：提交更新并递增 revision；
- 版本变化：拒绝静默覆盖，返回 conflict；
- 只追加的 Event/Evidence：使用幂等键安全重试。

### D4-004：协议兼容优先于内部重构

所有响应增加 `schema_version`，新增字段只做向后兼容扩展；破坏性变化通过明确的
协议版本或 capability discovery 暴露，不依赖客户端猜测。

## 4. 可迁移 Evidence

### 4.1 Evidence Locator

Evidence 的 source 拆成可迁移定位信息：

```text
locator_type: project_relative | git_blob | command | external
path: tests/result.log
project_root_hint: .
commit_hash: abc123
line_start/line_end: optional
content_hash: optional
```

`git_blob` 证据优先通过 commit 和相对路径定位；工作树文件通过相对路径和
`content_hash` 校验；外部来源必须标记为 external，并显示不可本地复核。

### 4.2 Evidence health

增加 Evidence 健康状态：

```text
reachable | moved | content_changed | missing | external | unknown
```

`brain doctor` 检查路径、commit、hash 和项目根目录，输出修复建议，但不自动修改
正式 Memory。用户确认路径迁移后，写入新的 Evidence locator 和审计 Event。

## 5. 搜索与索引

### 5.1 索引布局

候选方案按规模评测后择一作为默认实现：

1. 单一 FTS5 表增加可检索的 project partition 字段；
2. 每个项目一个 FTS 分区表；
3. FTS 表只存项目内数据，并由项目级数据库路由隔离。

选择标准不是单次 benchmark，而是：项目 A 的命中集合不能受项目 B 数据量影响，
且迁移、备份和删除项目不会破坏其他项目。

无论采用哪种布局，`SearchProvider` 的契约保持不变：

```text
search(project_id, query, scope, limit, as_of_commit?, as_of_time?)
```

### 5.2 搜索可观测性

`matches` 增加：

- provider；
- candidate_count；
- filtered_count；
- elapsed_ms；
- index_version。

这些字段用于诊断，不参与答案事实内容。搜索超时或索引损坏时，返回明确 warning，
必要时切换到项目内 LIKE fallback；fallback 也必须遵守 project_id。

### 5.3 评测集固定化

维护版本化 search fixture，至少覆盖：

- 精确命中；
- 中文 bigram；
- 同词跨项目；
- stale/版本回溯；
- scope 过滤；
- 无匹配；
- 项目 B 数据量大于项目 A 时的稳定性。

## 6. 并发与一致性

### 6.1 SQLite 运行策略

- WAL 保持开启；
- 设置有限 `busy_timeout`；
- 所有复合操作使用短事务；
- ingest 的 Event 使用 dedup_key 唯一约束；
- review_apply、handover 使用事务和 revision 检查；
- 事务失败时返回可重试/不可重试分类。

### 6.2 并发规则

| 操作 | 并发策略 |
|---|---|
| ingest Event | 幂等追加，重复返回已有 Event |
| curate | proposal 唯一键，重复返回已有 proposal |
| review_apply | proposal 单次终态，已处理不得覆写 |
| handover | 同一 task 使用 revision 或 task lease 防止丢更新 |
| snapshot | 可并发生成，basis commit 决定可比性 |
| verify/invalidate | 乐观锁，冲突要求重新读取 |

### 6.3 Task lease（轻量）

不做完整调度器，只增加可选 lease：

```text
owner_agent, lease_id, lease_expires_at
```

handover、完成和阻塞操作可以要求 lease；lease 过期后允许重新领取，但所有变更
仍保留 Event。未启用 lease 的项目保持 v0.3 行为。

## 7. CLI、MCP 与运行时体验

### 7.1 CLI 参数兼容

全局参数和子命令参数均支持：

```text
brain --db path verify --id D-001 --action verify
brain verify --db path --id D-001 --action verify
```

帮助信息明确参数作用域，错误信息给出正确示例。MCP 工具参数继续显式要求
`project_id`，不从隐式当前目录猜测项目。

### 7.2 非仓库环境

Git 探测先执行静默 capability check：

- 非仓库：返回 `git_available=false`，不打印 fatal stderr；
- 仓库：采集 commit、branch 和 changed files；
- Git 不可用：继续执行其他 ingestion，并返回 warning。

### 7.3 诊断命令

增加：

```text
brain doctor
brain doctor --fix-paths
brain migrate --dry-run
brain backup --output .brain/backups/...
brain restore --input ...
brain capabilities
```

`--fix-paths` 只修复用户明确确认的 locator，不修改 Memory 语义内容。

## 8. 备份、恢复和灾难处理

### 8.1 备份

备份必须包含：

- SQLite 数据库；
- schema version；
- config.json；
- exports；
- backup manifest 和 checksum。

支持完整备份和仅导出项目事实/证据/关系的可读 JSON/Markdown 备份。

### 8.2 恢复验证

restore 后自动执行：

1. schema integrity check；
2. project_id 计数核对；
3. link 两端存在性检查；
4. proposal 状态和审阅 Event 核对；
5. snapshot source_ids 可重建检查；
6. 固定 search fixture 回归。

恢复失败不得覆盖原数据库，先写入临时路径并返回报告。

## 9. API 与数据升级策略

### 9.1 Schema migration

schema_meta 增加：

```text
schema_version
minimum_reader_version
last_migration_id
```

迁移支持：

- `dry-run`：只报告将要改变的表和记录数；
- backup-before-migrate；
- 单向可审计 migration event；
- 失败回滚或保留迁移临时库。

### 9.2 能力发现

增加 `brain.capabilities`，返回：

- schema/API 版本；
- 可用 ingestion source；
- SearchProvider；
- 是否支持 lease、snapshot、review、backup；
- provider/index/model 版本。

Agent 可以据此决定是否调用可选能力，而不是根据版本号硬编码猜测。

## 10. 可观测性与审计

### 10.1 运行指标

按项目记录：

- memories/evidence/events/proposals/snapshots 数量；
- pending review 数量和等待时间；
- stale evidence 数量；
- ingest dedup 比例；
- review approved/rejected 比例；
- 搜索命中、无匹配、fallback 和耗时；
- backup/restore/doctor 最近结果。

### 10.2 审计约束

任何以下操作都必须有 Event：

```text
apply proposal
reject/defer/supersede proposal
verify/invalidate memory
move evidence locator
restore backup
acquire/release lease
```

审计 Event 只追加不更新，导出时保留原始 agent、session、时间和 reason。

## 11. 实施阶段

### Phase 1：便携性和当前瑕疵

- 移除证据健康检查中的硬编码绝对路径；
- 增加 project_root/path locator；
- 非 Git 目录静默探测；
- CLI 全局/子命令 `--db` 兼容；
- 增加机器迁移和非仓库回归测试。

### Phase 2：项目级索引

- 评测三种 FTS 分区布局；
- 选择并实现默认方案；
- 增加 index_version 和 doctor 检查；
- 验证其他项目数据量增长不改变命中集合；
- 保留正确的 LIKE fallback。

### Phase 3：并发和 revision

- WAL、busy_timeout 和短事务统一配置；
- Event/proposal 幂等唯一约束；
- Memory/Task/Proposal revision；
- review/handover/verify 的冲突响应；
- 可选 task lease。

### Phase 4：备份、恢复和迁移工具

- migrate dry-run 和 backup-before-migrate；
- backup/restore manifest；
- restore integrity report；
- doctor、capabilities 和运行统计。

### Phase 5：兼容性与真实项目试运行

- 固定 API/schema contract tests；
- MCP/CLI 双入口回归；
- 在两个不同目录、两台不同路径布局的项目上运行；
- 连续多日或多轮 session 试运行，记录 pending、stale、fallback 和恢复情况。

## 12. 测试与验收

### 12.1 必须通过的场景

1. 将 Brain 复制到不同绝对路径后，relative Evidence 仍可定位。
2. Git 仓库和 `/tmp` 非仓库都能 ingest，非仓库不产生 fatal 噪音。
3. `brain verify --db x` 与 `brain --db x verify` 行为一致。
4. 项目 B 增加 100 倍数据后，项目 A 的 search 结果集合不变。
5. 同一 Event、proposal、review 和 handover 并发/重试不会重复或丢写。
6. 两个 Agent 同时更新同一 Task 时，至少一方得到明确 conflict，不发生静默覆盖。
7. review_apply、verify、invalidate、locator 修复均有审计 Event。
8. backup/restore 后 counts、links、snapshots、search fixture 全部一致。
9. 迁移 dry-run 不改变数据库；迁移失败不损坏原库。
10. 旧 v0.3 客户端可以继续调用四个核心协议。
11. provider/index 不可用时，系统给出诊断信息并回退到安全路径。
12. capabilities 能准确描述当前可用功能和版本。

### 12.2 质量底线

- 跨项目污染率：0；
- 静默覆盖次数：0；
- 备份恢复后的 source/link 丢失：0；
- 非仓库 Git fatal 输出：0；
- 旧核心协议回归通过率：100%；
- 搜索结果受其他项目数据量影响的 case：0。

性能指标先以相对基线表达：在固定数据集上，项目数量和其他项目数据量增加时，
目标项目的命中集合保持不变，P95 查询耗时不超过当前基线的 2 倍；具体绝对阈值
在第一次真实项目试运行后确定。

## 13. 交付物

- `project-brain-v0.4.md`：本方案；
- 可迁移 Evidence locator 和 doctor；
- 项目级 FTS 索引及评测报告；
- revision、短事务、幂等和并发冲突处理；
- CLI 参数兼容与 capability discovery；
- backup/restore/migrate 工具；
- 固定 search fixture、contract tests 和双目录试运行报告；
- 更新后的 README、运行手册和故障排查说明。

## 14. 最终判断

v0.4 的完成标准不是“系统功能更多”，而是：

> **Brain 被复制、升级、并发使用或遇到局部故障后，仍然可定位、可诊断、可恢复、可审计，并保持 v0.2/v0.3 已经建立的项目隔离和事实边界。**

完成这一版后，才适合认真评估远程同步、团队权限、多租户服务和更大规模的语义检索。
