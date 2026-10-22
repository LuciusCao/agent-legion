本版收官 runs-async 里程碑：大批量添加条目与批量 job 操作改为秒级异步受理并新增任务中心；Studio agent 节点运行配置独立通道即改即存、可版本回滚；配套 velites 0.5.9（触顶自动续写 + 旧配置桥退役，含 Breaking）。完整工程记录见 `CHANGELOG.md` 的 0.8.1 段。

### Added
- 任务中心与大批量异步受理（#1117、#1157、#946）：workspace 新增「任务」页与 AppBar 徽标入口；添加条目超过 1000 条、或多选批量操作超过 1000 个 job 时秒级受理返回任务句柄，进度以卡片呈现（状态、进度条、逐 job 计数），进行中可暂停/继续/取消，终态可归档。
- Studio 运行配置通道（#1114 族，0.8.1 studio-split 里程碑）：agent 节点的 provider/model/thinking/prompt/tools/requires_labels 在 Inspector「运行配置」区即改即存，不再产生新 revision；配置版本历史可查看、可逐版本回滚；Studio Agent 建议卡拆为「运行配置变更」与「Workflow 草稿」两张卡分别提交。
- 批量操作列表面（#946）：任务中心混排展示批量操作与批量建 run 两类卡片，支持三视图（进行中/已结束/已归档）、逐 job 结果明细与打包产物下载；归档记录 90 天后自动清理。
- velites 0.5.9：单次输出触顶自动续写（#865）——回复因长度触顶时在同一 run 内续写，已落盘部分保留；OpenAI 兼容路径可按模型方言选择上限字段（#1093）。

### Changed
- 批量 job 单请求选择上限从 5000 上调至 20000（#1348），可经 `AGENT_LEGION_MAX_BATCH_JOBS` 覆盖。
- stepper 紧凑形态改按实测轨道宽度动态判定（#1335）：宽轨道下更多节点保持逐段渲染，窄面板下少量节点即切紧凑进度视图。
- **Breaking（velites，#1102）：** velites 不再读取 `~/.velites/config.json` 与 `VELITES_BASE_URL`/`VELITES_API_KEY`，`~/.velites/models.json` 成为唯一 provider 凭据来源；经 Worker 领取的任务不受影响。

### Fixed
- 并行 code 节点结果归因错配（#1311）：并行/分片节点共用固定结果文件名，失败节点的错误会串到同时完成的健康节点并误阻断下游；结果文件改按 execution 唯一命名。
- job_id 单射编码（#1255）：消除含下划线/斜杠拼接的跨 workspace id 碰撞及其死锁窗口；存量 id 原样可读，无需迁移。
- 执行清理的共享目录认领（#1315）：修复过期清扫误删在途 run 的运行目录、以及同批过期目录永久泄漏两形态。
- 草稿与发布链路（#1114、#1196 等）：草稿「与编辑器不一致」提示的单帧误消、前后端空白判据不一致导致的确定性 422、runtime-only 发布的确认死端等修复。
- 其他：节点日志跨 job 同名碰撞（#1113）、生产关池归还丢弃（#1077）、Studio 会话孤儿投影补发（#1095）、worktree 派生名撞名加锁（#1084）、skill 共享目录写入防符号链接重定向（#1254）等。

### Security
- KaTeX 0.17.0 → 0.18.10、React Router 6.30.x → 7.18.4（#1329、#1330，0.8.0 已知后续收官）：修复 GHSA-238p-pmpm-9mq7 与 GHSA-337j-9hxr-rhxg / GHSA-wrjc-x8rr-h8h6；React Router 为跨大版本迁移，Link/useNavigate 目标已全量盘点为内部路径。

### 升级注意
- 先备份 PostgreSQL；Host 启动自动执行 schema v99–v103，迁移只向前。必须先升 Host 到 v0.8.1 并确认健康，再逐台升 Worker 到 worker-v0.8.1；Worker 协议保持 v6，滚动窗口内旧 Worker 可继续领取任务。
- Worker 的 velites 升至 0.5.9：仍依赖 `~/.velites/config.json` 或 `VELITES_BASE_URL`/`VELITES_API_KEY` 直调 velites 的环境，升级前先按 `docs/architecture/velites-model-registry.md` 的迁移节写好 `models.json`；models.json 的 `outputTokensParam` 键会被 0.5.9 之前的 velites 拒绝，先升级再写入。
- `scripts/install-worker.sh` 默认版本已钉 worker 0.8.1 / velites 0.5.9。

**Full Changelog**: https://github.com/LuciusCao/agent-legion/compare/v0.8.0...v0.8.1
