本版补齐批量异步操作的暂停、继续与取消控制面，并将任务中心独立为完整页面；同时新增统一的 `execution_type` 节点契约，修复快速恢复、历史卡片、停喂提示及执行徽标与 Studio 风险口径。完整工程记录见 `CHANGELOG.md` 的 0.8.3 段。

### Added
- 批量操作控制面（#1397）：任务中心与 API 均支持 pause/resume/cancel；暂停或取消阻止后续 chunk，恢复后继续按原操作推进，逐 job 结果与审批审计保持完整。
- 独立任务中心页面（#1391）：active 与 archived 视图使用统一应用导航，可返回当前 workspace 或首页；原任务中心 URL 不变。

### Changed
- 节点执行类型契约 Phase 1（#1367）：job 详情与 Studio job context 新增 `execution_type`（`code` / `agent` / `null`）；旧 `executor_id` / `executor_kind` 进入弃用窗口但暂时继续返回，前端已切换至新字段。

### Fixed
- 快速暂停再恢复（#1397）：恢复重查保留原流式快照，不再漏执行 job；package 复用已生成 output，不再重复打包。
- 任务中心可见性（#1392、#1393）：完成批次会立即进入 ended 历史视图；backlog 水位造成的停喂会在卡片上明确显示，不再表现为无说明地停滞。
- 徽标与风险口径（#1365、#1366、#1369、#1381、#1402、#1412）：start/approval 不再误显示 code chip，agent 标题与执行徽标去重；摘要、发布复核和变更计数统一按结构侧风险展示。
- approval 配置保留（#1410）：Studio 切换节点类型或清洗 YAML 时保留 verdict `config_schema`，与 schema v104 的审批契约一致。

### Maintenance
- 内部写入与门禁治理（#1394、#1407、#1374、#1375、#1376、#1379、#1380、#1385、#1408）：退役 legacy `record_remote` 测试写入 helper，收紧架构检查器豁免与 aff 全量回退，并清理前端死导出。
- 测试与文档（#1364、#1368、#1377、#1382、#1387）：补齐契约参数化，硬化时序敏感测试并摘除已修复 flake 注册，校准锁序与认证失败 marker 文档。
- tower worktree 兼容（#1409）：tower 子 worktree 复用父实例资源，测试库按父子实例隔离，初始化和清理流程支持嵌套布局。
- 历史归属校准（#495）：纯本地 shard 的 `node_code` 修复实际由 #886 在此前版本交付；本版仅完成里程碑对账，不包含该问题的重复运行时变更。

### 升级注意
- **数据库：** 本版无新 schema 迁移，保持 v104；仍建议升级前备份 PostgreSQL，已由 0.8.2 完成 v104 的实例无需额外迁移。
- **`execution_type` 迁移：** 新调用方立即改读 `execution_type`；Worker 身份读 `worker_id`，Agent 身份读 `agent_id`。旧 `executor_id` / `executor_kind` 将在下一完整发布周期的 Phase 2 移除，请勿新增依赖；`execution_type=null` 且 `route_error` 非空时应按路由配置错误处理。
- **批量控制面：** pause/resume/cancel 的粒度是 chunk，不是单 job 即时取消；已开始的本 chunk 会提交结果，已完成的逐 job 变更与审批 decision 不回滚，已开始的打包完成钩也不保证被中断。paused 不是终态，客户端应继续依据 operation 状态和逐 job 结果判断完成。
- **Host / Worker / velites：** 本版没有 `worker/`、`shared/` 或 `velites/` 行为变更，不发布 worker-v0.8.3 或新 velites tag；Host 升级后继续使用 worker-v0.8.2 与 velites-v0.5.9，一键安装默认钉版保持 worker 0.8.2 / velites 0.5.9。frontend 本次不单独发版，独立版本保持 0.4.0-alpha。
- **nightly gate 显式豁免：** 2026-10-26 的 nightly-e2e 与 ci-extended 通过，但 deps-audit 检出 certifi 2026.5.20 的 PYSEC-2026-4320 与 filelock 3.29.0 的 CVE-2026-112940 / CVE-2026-112913 / CVE-2026-112907，exemption-expiry 另有 FLAKY-007 / FLAKY-009 / FLAKY-010 / FLAKY-011 / FLAKY-019 过期；这些属于发布内容之外的依赖与测试治理项，本版明确豁免而不宣称 nightly 全绿，后续应升级依赖并逐项修复或评审延期。

**Full Changelog**: https://github.com/LuciusCao/agent-legion/compare/v0.8.2...v0.8.3
