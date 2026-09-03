# jobs.workflow_definition_snapshot_json 瘦身：评估结论与迁移草案（#354 方案 3）

状态：**设计草案**（本 PR 只落地 #354 的方案 1/2/4；方案 3 的 schema 迁移
单独成 PR，本文是它的依据）。行号/文件名以本文撰写时的 develop 为准。

## 现状

- `jobs.workflow_definition_snapshot_json`（多 KB TEXT）在 intake 时由
  `create_jobs_bulk`（`server/app/jobs/queries/job_bulk.py`）逐 job 冻结写入，
  同一 run 的几千个 job 各存一份完全相同的定义快照（`revision["definition_json"]`）。
- `jobs.workflow_revision_id` 已存在且同一 run 内全部相同；`workflow_revisions`
  是 append-only 的版本化表（active 归档为 archived，**从不删除**），
  `definition_json` 即同一份内容。快照是纯冗余副本。
- 读路径已经为快照缺失做了兜底：`definition_from_job_snapshot` 返回 None 时
  调用方一律回退 workspace 的 active revision（`revision_format.py` docstring
  声明的契约）。也就是说「快照列改为引用 + 按需 join」的读侧改造面是**收敛
  的、有限的**。

## 为什么拆出去（不做进本 PR）

1. **回填不划算**：历史行的快照与 revision 行 content-identical（intake 即从
   revision 复制），回填 `''` 需逐行 UPDATE 全表——在 ~10^6 job 规模上这是一次
   全表重写，收益（磁盘）与风险（长事务、WAL 洪峰、与在线 claim 竞争）不成
   比例。**结论：只对新行生效，历史行随 retention sweeper（本 PR 方案 2）自
   然滚出**，不需要回填迁移。
2. **写路径有一个语义决策要单独评审**：见下节「intake 冻结语义」。

## 目标形态（新行不再存快照副本）

- `create_jobs_bulk` / `create_job`（`job_nodes.py`）/ `upgrade_job_workflow`
  （`workflow_upgrade_mutation.py`）写 `workflow_definition_snapshot_json=''`，
  只保留 `workflow_revision_id` / `workflow_definition_hash`（后者继续用于
  scan marks 的缓存键与「job 是否仍匹配 active revision」的判断）。
- 读路径按需 join：新增一个窄查询
  `get_workflow_revision_definition_by_id(revision_id)`（挂在
  `WorkflowRevisionReadQueriesMixin`），消费点逐一改造：

| 消费点 | 现行为 | 改造 |
| --- | --- | --- |
| `workflow_worker/ready_cache.resolve_cached_definition` | 快照缺失时已回退 `get_workflow_snapshot_for_hash`（按 hash 从任意 job 借快照） | 新行快照恒空 → 回退改为按 `workflow_revision_id` 读 `workflow_revisions.definition_json`（PK 点查，一次 per hash 进程内缓存，语义不变） |
| `jobs/queries/job_nodes.finish_node_run` | `definition_from_job_snapshot(dict(job))` | join revision（job 行已在手，revision id 点查） |
| `jobs/queries/job_scan_marks.get_workflow_snapshot_for_hash` | 从 jobs 借快照 | 改读 `workflow_revisions`（按 hash 点查，需确认 hash 唯一性，见风险） |
| `services/node_code_pins.node_code_pins_from_job_snapshot` | 从 job 快照抽 `node_code_pins` | 改从 revision `definition_json` 抽（同一 JSON，pins 本就住在 revision 里） |
| `services/job_rerun/*`、`services/quality_replay_setup`、`services/job_workflow_upgrade` 的 skip 判断 | 读 job 快照列 | 同样改为 revision 点查；quality replay 的 copy job 需要显式取 revision 行构造 `revision` dict（本就传了 `workflow_revision_id`） |
| `workflow_worker/ready_cache` 的 `node_code_pins` 传递（`lean_job["node_code_pins"]`） | 从快照抽取后塞进 lean job | 改从 revision 抽取，键名不变 |

- **intake 冻结语义**：现快照的意义是「job 冻结 intake 时刻的 revision」。
  由于 `workflow_revision_id` 同样在 intake 冻结、`workflow_revisions` 行
  不可变且永不删除，join 取回的与快照**逐字节相同**——语义无损。唯一的失效
  场景是人工删 revision 行（全库无此代码路径），与 v62「workspace id 终身
  绑定」的治理假设一致。

## 迁移草案（单独 PR）

schema 版本 v72，纯 DDL、无数据回填：

1. 无需新列：`workflow_revision_id` / `workflow_definition_hash` 已存在。
   建议补一个覆盖点查的索引
   `create index if not exists idx_workflow_revisions_definition_hash on workflow_revisions(definition_hash)`
   （`get_workflow_snapshot_for_hash` 与 job→revision 一致性校验用）。
2. `migration_registry.py` 登记
   `SchemaMigration(72, "jobs_definition_snapshot_new_rows_only")`（DDL-only，
   无 apply fn，与 v60/v61/v63 同模式）；历史行不动，`postgres_schema.sql`
   的列定义保留（列本身不 drop——drop 要等历史行滚出后的 v7x 再议，且
   `select *` 的消费点必须先全部显式化）。
3. 窗口期策略：迁移上线后新 job 快照列为空，读路径的 revision 回退分支成为
   主路径；历史 job 继续用自身快照（不受影响）。两个分支并存是设计内的
   过渡态，与 v62 wire 字段等价替换 → v70 退役的先例同构。
4. 测试面：`tests/workflows`、`tests/workers`（ready_cache/scan 的快照缓存）、
   `tests/services/test_node_codes.py` 的 pins 断言需各加「新行无快照」用例。

## 风险与未决

- `workflow_definition_hash` 在 `workflow_revisions` 表上是否全局唯一未经约束
  保证（hash 覆盖纯 definition、pins 不入 hash，理论上同一定义重发布会撞
  hash——但重发布会归档旧行，`get_workflow_snapshot_for_hash` 借快照时本就
  任取其一，语义等价）。迁移 PR 需决定：按 revision_id 点查为主（hash 仅作
  缓存键），避免对 hash 唯一性的隐含假设。
- `finish_node_run` 在结果提交事务里多一次点查，量级为 PK lookup，可接受；
  但需保证 join 失败不改变结果提交语义（revision 缺失时降级 active revision，
  与现在 corrupt-snapshot 的降级路径一致）。
