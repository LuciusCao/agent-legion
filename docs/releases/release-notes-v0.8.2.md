本版收口批量人工审批与大批量操作：新增批量通过/终止、失败项重试和取消数量上限的流式筛选；材料上传改为 create-only 条件写，存量无 hash 材料自动建立 ETag 身份，孤儿 GC 按 key 文法安全重开或收紧删除批。完整工程记录见 `CHANGELOG.md` 的 0.8.2 段。

### Added
- 批量审批（#1363）：现有任务列表多选后可直接「通过」或「终止」，也提供 `batch-approve` / `batch-reject` API；大选择转后台执行并在任务中心查看逐 job 结果。每个成功决策仍独立审计；需要人工填写 payload 或有多个待审批门的 job 会明确跳过，不会被批量放行。
- 批量操作失败项重试（#1358）：新增 `POST .../job-batch-operations/{op_id}/retry-failed`，把终态操作的失败项按原 kind 和参数续跑为带溯源的新任务；pause/resume 与批量审批不支持该入口。

### Changed
- 批量选择取消单请求数量上限（#1362）：大 filter/failure 选择改为受理时冻结筛选条件与 keyset 上界、后台增量补页，不再返回 `batch_selection_too_large`；小选择同步快路径不变，在途执行负载继续受 backlog 水位约束。
- 材料孤儿 GC 重开删除批（#1314）：inline 文本与 demo seed 先建行再上传，`gc-s3-jobs.py --apply` 可删除符合单次使用 key、年龄下限与删除前反查三道门禁的材料孤儿；旧文法材料仍只报告。
- DAG 执行徽标改用现行类型词汇（#1180、#731）：code 节点不再双「code」，agent 节点显示 `agent` 及可用的 runtime，执行徽标只显示真实 worker 或 agent 身份；API 的 `executor_kind` 摘除已退役 pi/openclaw 枚举。

### Fixed
- 并行与分片 code 节点（#1355、#1356）：Host 本地分片现在能读取 `shard_index` / `shard_input`，认证失败 marker 按 execution 隔离，邻节点不再互删或错认。
- 材料 ETag 门禁出口（#1320）：没有在线 v6+ Worker 时，受门禁任务可回落 Host 本地执行；纯远程形态保持 pending 且不占 code stock，v6 Worker 上线后自动恢复。
- job 并发与删除（#1346、#1357）：多 job 路径统一按真实 advisory 锁键排序，关闭 hash 碰撞导致的 AB-BA；删除广播前复核同源 job 未重建，避免客户端误删新 job。
- Studio 草稿与运行配置双通道（#1352、#1353、#1359、#1360）：摘要面板正确过滤 execution-only 变更，纯 code workflow 不再误判 runtime 结构变更，4xx 校验不再退避重试，「未发布变更」瞬态提示可正常渲染。
- 其他：节点日志目录按 job 确定性分片（#1340）、材料重绑 CAS 复核代次与状态（#1313）、撞名 staging 前缀的材料宽限窗（#1322）、worktree 清理中断后的派生库收养防护（#1349）。

### Security
- 材料上传条件写（#1321）：presigned PUT 签入 `If-None-Match: *`，同一 URL 首次上传后不可重放覆盖，complete 后的内容替换由存储层直接拒绝。
- 存量无 hash 材料身份绑定（#1316）：Host 启动时按当前对象 HEAD 绑定 ETag 并轮换上传代次；绑定后旧 Worker 永久不能领取相关材料任务，防止旧缓存绕开内容核验。
- legacy 固定 job authority key 退出 GC 删除批（#1309）：只有一次性版本 key 可删除，旧固定 key 仅报告，关闭同 job 重建时的误删窗口。

### Maintenance
- quick gate、nightly 与架构守卫补强（#1307、#304、#1331、#1333、#1308、#1354、#732）：覆盖 aff 空选择与 postgres 提示、nightly 失败追踪、嵌套池 checkout、零引用导出及 frontend emoji 检查。

### 升级注意
- **数据库：** 先备份 PostgreSQL；Host 启动自动执行 schema v104，加入批量审批审计字段与 operation kind。迁移只向前，回滚使用升级前备份。
- **Host / Worker：** 必须先升 Host 到 v0.8.2 并确认健康，再逐台升级 Worker 到 worker-v0.8.2；协议保持 v6，但本版 worker 线含并行 code 节点 marker 修复。pre-v6 Worker 不能领取已绑 ETag 或 repair 中的无 hash 材料任务，纯远程车队中这些任务会等待升级，不占 code stock。
- **配置与 API：** `AGENT_LEGION_MAX_BATCH_JOBS` 已失效，设置后仅打 deprecation warning，请从部署配置移除；自写客户端不要依赖旧的批量选择 422 上限错误，应消费任务句柄与逐 job 结果。
- **对象存储与上传客户端：** 材料存储必须支持签名 `If-None-Match: *` 条件 PUT；自写客户端必须携带该头，缺头为 403、同 URL 重放为 412，网络重试遇 412 按已上传处理。SeaweedFS 4.45 已实测，RustFS 尚待验证，使用该后端前必须先完成条件写验证。
- **绑定与 GC：** 采用停机、单副本 Host 升级；启动绑定时保持对象存储可用，失败项可用 `scripts/bind_legacy_material_etags.py` dry-run → `--apply` 补跑。材料 GC 现在具备真实删除能力，请先 dry-run 审查，再执行 `scripts/gc-s3-jobs.py --apply`。
- **一键安装与 velites：** 默认版本钉到 worker 0.8.2 / velites 0.5.9；velites 子树自 velites-v0.5.9 无差异，本版不发布新 velites tag。

**Full Changelog**: https://github.com/LuciusCao/agent-legion/compare/v0.8.1...v0.8.2
