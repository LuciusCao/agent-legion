本版修复 Agent 批量结果完成与心跳之间的 lease 死锁，并堵住 Worker 压缩事件与应急证据中嵌套 JSON key 的密钥泄露边界；同时完成 Host、Worker、velites 与治理面的结构拆分。完整工程记录见 `CHANGELOG.md` 的 0.8.6 段。

### Fixed
- heartbeat / result finish 死锁（#1449）：集中预取并按统一顺序获取 job 与 lease 锁，消除批量心跳和结果完成交叉等待；结果归属、代次与提交语义不变。

### Security
- Worker JSON key 脱敏（#1175）：压缩 Pi events 与应急证据现在同时处理嵌套字符串 key 和 value，key 碰撞时整行 fail-closed，不再让已注册密钥经 key 原样进入归档；Host 与 Worker 都需升级。

### Maintenance
- Skill contract 与 velites 拆分（#994）：Python、前端和 velites 的 contract parser/validation/advisory 测试按职责归位，root contract 优先级、legacy 兼容与公开契约不变。
- Host 与 Worker 预算回落（#751、#993、#1002、#1003、#1009、#1010、#1011、#1012、#1016）：拆分 API、认证、执行面、数据库、workflow、MCP 与 heartbeat registry，不改变 API、迁移、事件或并发协调语义。
- 架构治理（#1005、#1008、#1030）：清理预算豁免并统一 migration root 策略；migration 文件仍受 raw 800 行上限，逐文件 ceiling/ratchet 豁免不改变 schema 或升级行为。
- CI 沙箱门禁（#1491）：backend-unit 在 Ubuntu 24.04 运行真实 bubblewrap 隔离回归并修正探针，仅影响 CI 与测试，不构成原生 Linux 生产支持声明。

### 升级注意
- **数据库与回滚：** 无新 schema 迁移，保持 v104；升级前备份 PostgreSQL，回滚使用与目标 schema 匹配的备份。#1030 只调整维护门禁，不改变 migration 执行或最低升级版本。
- **三产品线配套：** 本次发布 Host/main v0.8.6、worker-v0.8.6 与 velites-v0.5.11，三条线自各自上一 tag 均有实际源码差异。先升 Host 并确认健康，再逐台升级 Worker 与 velites；协议保持 v6，但不能因此沿用旧 Worker/velites。
- **安装钉版：** 一键安装默认更新为 worker 0.8.6 / velites 0.5.11；等待对应 Worker 镜像和 velites 三平台产物发布后再执行默认升级，现有配置、显式资源 limits 与 pending upload 保留。
- **frontend 与发布前置：** frontend 不单独发版，继续保持 0.4.0-alpha；release PR 合并后须先在对应 main 精确提交上取得 Nightly Gate 全绿，再创建三个非空 tag 与 Releases，本流程不执行生产部署。
- **原生 Linux 已知后续：** #39、#1049 保持 open，原生非 root systemd 与真实 Host/Worker 迁移、回滚验收尚未完成。

**Full Changelog**: https://github.com/LuciusCao/agent-legion/compare/v0.8.5...v0.8.6
