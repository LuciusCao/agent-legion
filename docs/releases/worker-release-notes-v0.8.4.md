本版要点：Worker 各 Compose 形态新增实测 256MiB / 128 pids 默认资源限额与并发 envelope 守卫，一键安装升级按实际控制卷预检并保留显式配置与 pending upload；移除 agent 并发静态容量误报，配套 velites 0.5.10 修复 OpenAI 工具错误标记透传。

配套要求：先升级 Host 至 agent-legion v0.8.4 并确认健康，再升级 Worker 至 worker-v0.8.4 与 velites-v0.5.10；协议保持 v6，仍须按 Host→Worker 顺序配套升级，不能以协议号未变替代版本升级。默认 limits 只覆盖 agent≤10、code=0、upload≤4，超出时须评审并显式设置 limits 与 `AGENT_WORKER_RESOURCE_LIMITS_MODE=explicit`。原生非 root systemd/AppArmor 验证仍属 #1049/#39 后续范围，本版不宣称完成。深记录见主仓 release：https://github.com/LuciusCao/agent-legion/releases/tag/v0.8.4

**Full Changelog**: https://github.com/LuciusCao/agent-legion/compare/worker-v0.8.2...worker-v0.8.4
