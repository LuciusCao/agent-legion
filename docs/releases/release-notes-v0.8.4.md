本版为 Worker 各 Compose 形态落地经原生 Linux 实测的 256MiB / 128 pids 默认资源限额与升级预检，并修复 velites OpenAI 兼容路径的工具错误透传；另含任务中心窄屏排版、Worker 静态容量误报移除和一批测量/诊断与内部维护工具。完整工程记录见 `CHANGELOG.md` 的 0.8.4 段。

### Added
- Worker 容器资源限额（#1049 的 Docker/Compose 子集）：默认 `mem_limit=256m`、`pids_limit=128`，仅覆盖 agent=10、code=0、upload=4 的测量 envelope；启动、API 与热更会拒绝超 envelope 的 default 配置，不静默降低并发，也不改变既有身份、权限或 CPU 配置。
- 资源测量工具（#1049）：新增原生 Ubuntu measurement workflow、cgroup v2 采样、直接上传与受控 backlog、重启及故障注入证据；Docker Desktop 只用于功能验证，不用于 sizing。

### Fixed
- OpenAI 兼容工具错误（#450）：velites 在 `is_error=true` 的 tool result 文本前添加 `[ERROR]`，模型可以可靠区分工具失败与普通输出；Anthropic 路径与事件流不变。
- Worker 容量误报（#1126）：移除按 CPU 核数固定倍率判断 agent 并发的启动 WARNING，保留基于实际 load1 的动态回压。
- 窄屏排版（#1422）：任务中心标题与卡片底行在小屏纵向堆叠，并修复 shell fade 横向溢出；Host 镜像同时补齐 Skill checkout 所需的 `git`。

### Changed
- 一键安装升级预检（#1049）：安装器按实际控制卷与 Worker YAML 语义校验资源 envelope，失败时保留旧 compose/.env；升级或回滚不覆盖显式 limits、控制配置或 pending upload。

### Security
- 依赖安全升级：certifi 升至 2026.10.23，修复 requests 真实 TLS 链涉及的 GHSA-6q48-6c7w-9gw2 / PYSEC-2026-4320；filelock 升至 4.2.7 并增加 `filelock>=4.2.1` 下限，修复 9 个 CVE，包括直接覆盖项目 POSIX FileLock 的 CVE-2026-112909。本版不重复 0.8.3 的漏洞豁免。

### Maintenance
- FLAKY 注册清理：依据两轮 backend-coverage 零 rerun 与原始 JUnit 证据，删除 FLAKY-006/007/009/010/011/012/013/014/019/020 共 10 条过期注册，不再延长 deadline；FLAKY-021 保留，已删条目若复发将按未注册 rerun 硬失败。
- velites 与 Worker 内部重构（#996、#995、#311、#451）：拆分入口、输出/JSON 工具、测试与 supervisor/CLI payload，回收体积豁免，不改变运行时契约；`config.rs` 退役属于此前版本，本版不重复声明。
- 贡献者与 UI 维护（#723、#999）：补齐门禁分层与慢测试指引，StudioChatPermission CSS 独立成模块且视觉不变。
- 原生沙箱诊断（#39、#1049）：新增 stock Ubuntu 24.04 权限矩阵和原始证据契约；当前硬边界结论仍为 no-go，该工具不构成生产修复。
- 历史归属（#507）：原生 velites 二进制维护已由此前 #835 交付，本版只完成里程碑对账，不包含重复运行时变更。

### 升级注意
- **数据库：** 无新 schema 迁移，保持 v104；仍建议升级前备份 PostgreSQL，回滚使用与目标 schema 匹配的备份。
- **升级顺序：** 先升 Host 到 v0.8.4 并确认健康，再逐台升 Worker 到 worker-v0.8.4 并配套 velites-v0.5.10。协议保持 v6，但新 Worker 仍会拒绝低于 v6 的旧 Host，不能反向先升 Worker。
- **资源 envelope：** agent>10、code>0 或 upload>4 的部署必须在升级前评审并显式设置 `AGENT_WORKER_MEM_LIMIT` / `AGENT_WORKER_PIDS_LIMIT` 与 `AGENT_WORKER_RESOURCE_LIMITS_MODE=explicit`；明确无限额使用 `0` / `-1`，不要直接继承默认值或删除 guard。
- **安装钉版：** 一键安装默认钉到 worker 0.8.4 / velites 0.5.10；必须等对应 Worker 镜像和 velites 三平台产物发布成功后再执行默认升级。frontend 不单独发版，继续保持 0.4.0-alpha。
- **原生 Linux 已知后续：** #1049 与 #39 已转入「Linux 部署」里程碑并保持 open；原生非 root systemd、targeted AppArmor、no-network 沙箱及真实迁移/回滚验收均未完成。PR #1453 仅保留诊断证据，不得据此宣称原生 Linux 生产支持已落地。
- **nightly gate 发布前置：** 2026-11-02 的 run 41383299051 因依赖漏洞与 10 条过期 FLAKY 注册失败，本版已实际升级 certifi/filelock 并清理对应注册，不作安全豁免。release PR 合并后仍须先在修复后的 main 上手动运行 nightly 并取得全绿，再创建三个产品线的 tag 或 GitHub Release。

**Full Changelog**: https://github.com/LuciusCao/agent-legion/compare/v0.8.3...v0.8.4
