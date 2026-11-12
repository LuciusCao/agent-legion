本版集中完成 Studio 与实例设置预算回落，修复 Worker Key 使用统计与当前 workspace 可见范围不一致，并升级 CI action 依赖；另记录原生 Linux targeted AppArmor candidate 的 no-go 实验边界。完整工程记录见 `CHANGELOG.md` 的 0.8.5 段。

### Fixed
- Worker Key 使用统计（#1172）：「N 个 Worker 使用」只统计服务当前 workspace 的消费者，不再出现被计数却在当前列表不可见的 Worker；删除 Key 的告警仍保留全实例消费者，避免低估级联影响。

### Maintenance
- Studio 与实例设置重构（#1000、#1004、#1013、#1014、#1015、#1017）：拆分预览/DAG、草稿同步/保存/发布、Inspector、query key 与实例设置契约，清退预算豁免；既有 API、草稿发布、运行配置和交互语义不变。
- CI action 更新（#1461–#1463 及同期更新）：升级 release、uv、Docker、Node 与 Rust cache actions，继续按完整 commit SHA 钉版；被集中提交覆盖的 Dependabot PR 不重复计为合并项。
- 原生沙箱实验（#39、#1049，PR #1458）：targeted AppArmor direct-bwrap candidate 在 payload 启动前失败，结论为 candidate-only no-go，rollback 已恢复 stock 状态；未修改生产沙箱或部署权限。
- 历史归属（#1199、#1001）：双通道分类与普通节点配置语义已由此前变更覆盖，旧 AgentEditor 工具 picker 目标已随组件退役而失去适用对象；本版只对账，不重复声明新功能或修复。

### 升级注意
- **数据库：** 无新 schema 迁移，保持 v104；仍建议升级前备份 PostgreSQL，回滚使用与目标 schema 匹配的备份。
- **Host / Worker / velites：** `v0.8.4..main` 的 `worker/`、`shared/`、`velites/` 无产品差异，只发布 Host/main `v0.8.5`，不创建 `worker-v0.8.5` 或新 velites tag。先升 Host 到 v0.8.5 并确认健康，再逐台维护 Worker；Worker 继续使用 worker-v0.8.4 与 velites-v0.5.10，协议保持 v6，无需为主仓版本号重装。
- **安装钉版与 frontend：** 一键安装默认保持 worker 0.8.4 / velites 0.5.10；frontend 不单独发版，继续保持 0.4.0-alpha。
- **nightly gate 发布前置：** release PR 合并后须先在对应 main 精确提交上运行 Nightly Gate 并取得全绿，再创建 `v0.8.5` 与主仓 GitHub Release；本流程不生成 worker/velites 空版本，也不执行生产部署。
- **原生 Linux 已知后续：** #39、#1049 与 Linux 部署里程碑保持 open，#1470、#1471 分别跟进 probe CI 状态分离和并发 audit/rollback 证据完整性；原生非 root systemd 与真实部署验收尚未完成，不得宣称原生 Linux 生产支持。

**Full Changelog**: https://github.com/LuciusCao/agent-legion/compare/v0.8.4...v0.8.5
