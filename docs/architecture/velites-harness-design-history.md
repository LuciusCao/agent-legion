# velites harness 设计历史（动机、灰度路径、里程碑与开放问题）

> **历史设计记录**（#1103 自 [velites-harness.md](velites-harness.md) 拆出）：本文保留 velites
> 立项时的动机、升格期的灰度路径、里程碑计划与当时的风险/开放问题，均为时点快照
> （原文日期 2026-07-31，2026-08-03 升格落地、2026-08-04 金丝雀关闭）。现行规格——事件
> schema、可控性与沙箱、CLI、provider、工具、集成形态与 runtime 接入指南——以
> [velites-harness.md](velites-harness.md) 为准；文中章节号（§4/§5/§8/§9）指该现行文档。
> 前置：[PoC 报告](velites-poc-report.md)、[M2 对照验证](velites-m2-validation.md)、
> [升格实施计划](velites-runtime-promotion.md)。

## 1. 背景与动机

当前 worker 上每个节点执行 = 冷启动一个 Node Pi 进程。高并发 worker 形态下的实测（2026-07-31）：

- 每个 pi 进程常驻数十至上百 MB、峰值可达数百 MB，数十并发即占用数 GB 内存；
- 每次节点执行都要付 Node 启动 + 模块加载（实测 1.5–1.7 s CPU），执行量大时累积成显著开销；
- `--mode json` 的 `message_update` delta 占 stdout 体积 99%+，Pi 侧序列化、worker 泵逐字节
  扫描后全部丢弃——协议层面的纯浪费，且无法通过配置关闭（PoC 已确认）。

PoC（pi_agent_rust 替换验证）同时证明了两件事：

1. 收益空间是真实的：同负载 Rust 实现 RSS ≈ 1/6.5、启动 CPU ≈ 0；
2. 但我们对 harness 的需求面极窄（3 个工具 + skill 注入 + 事件流 + 一个 OpenAI 兼容
   provider），fork 一个 30 万行、以插件/TUI 为主体的移植版是背着债务起步。

因此决策：**自研 velites**，范围严格限定在 Agent Legion 的真实消费面内。

## 2. 切换期决策（原 §9 历史段落）

**flavor 的退役（2026-08-05）**：`workflows.pi.flavor` 实现选择层已随 yaml
块一并删除。此前保持 `runtime: pi` 的 4 个业务视频 agent 已由
schema v27 migration 翻转为 `runtime: velites`（新发 published 版本、归档
旧版）。`PiRuntimeConfig` 当时只剩硬编码默认（flavor="pi"，该配置类亦已于 2026-08-26 死代码清理中删除）；此前专供的本地
pi executor 死路径（`executors/pi.py` + `PiRunner` 及
pi_config/pi_command_builder/pi_prompt 链）已整体删除（#108）。

**pi 的定位（2026-08-04 用户决策）**：pi **不退役**，作为可选 runtime 长期
保留——velites 是生产主力，pi 作为备选实现与对照基线继续可用
（`runtime: pi` 即完整 pi 路径）。若未来仅出于卫生目的清理
（如 command_spec version 升级），另行立项评估，与退役无关。

**历史灰度路径（已完成，存档）**：

- Phase 0：契约测试 + 真二进制集成测试入库（M4）；
- Phase 1 shadow = 抽样回放（当时的离线回放脚本双跑 pi 与 velites，diff 事件流与
  产出；该脚本已不在仓库中）；
- Phase 2 金丝雀 = 全局 `flavor: velites` + worker capacity 压低起步，逐步
  恢复至生产量级并发（整夜跑批验证、成功率高）；
- 升格落地（2026-08-03，PR #20/#21）：runtime 枚举/dispatch/sweeper/runtime
  维度 + Worker UI/预检；审题链路迁 `runtime: velites` 并整夜跑批验证
  （生产量级节点量）；
- 金丝雀关闭（2026-08-04，`14ec130f`）：`flavor: velites` 与审题链路
  `runtime: velites` 落为 tracked 默认值。

openclaw 曾按现行接入指南的步骤完整接入（adapter、Worker 事件合成层、
e2e），后按用户决策整体退役——实现与拆除过程见 git 历史（#75）。

## 3. 里程碑

| 里程碑 | 内容 | 验收 |
|---|---|---|
| M1 骨架 | crate 初始化、CLI、事件 emitter、stub provider 的 agent loop（read/write/bash） | cargo test 绿；stub 下 golden 事件序列 |
| M2 契约对齐 | OpenAI 兼容 SSE provider、skill 加载、错误/重试语义 | 真 gateway 跑 `review_subtitles` fixture，与 Node Pi diff 通过 |
| M3 可控性 | 预算/取消/输出自检 + 工具体积度量 | 三条 invariant 测试入库 |
| M4 集成 | flavor 配置、Dockerfile rust stage、CI rust lane、集成测试 full lane | `./scripts/check-quick.sh` + full gate 绿 |
| M4.5 沙箱 | §5 沙箱小节：Sandbox 抽象、macOS seatbelt 先行、Linux bwrap 在 worker 镜像验证、`EXEC-HARNESS-SANDBOX-001` | 沙箱集成测试入库（quick lane）；逃逸尝试全部被拒 |
| M5 灰度 | shadow → 金丝雀 → 默认 | 生产高并发形态下 RSS/CPU 对比报告 |

## 4. 风险与开放问题（立项时）

- **上下文体积护栏（已拍板：pi 对齐截断）**：原疑虑是截断可能伤 agent 表现、且
  阈值缺乏依据。2026-08-01 决策：直接对齐 pi 的成熟策略——2000 行 / 50KB
  （50×1024 字节）双阈值先到即截，read 截头、bash 截尾并落临时文件，提示语与
  pi 一致（细节见现行文档 §8）；截断不切断行（bash 末行单行超限除外）。`output_bytes`
  继续记录截断前体积，金丝雀期间观察截断触发率与 agent 表现，必要时再调阈值；
- **SSE 方言**：gateway 背后不同模型的 SSE 细节差异（PoC P2）——M2 用真实模型矩阵
  验证；缓解：解析器对非标准 event 行容错跳过；
- **thinking 参数映射**：不同后端 wire 参数不同——初期只支持 gateway 当前映射，
  新后端接入时显式扩展；
- **prompt 兼容性**：SKILL.md 中的指令对模型行为的引导经 Pi 验证过，velites 的
  system prompt 拼装顺序不同可能改变行为——M2 diff 不仅比事件结构，也抽查产出质量；
- **工作量估计**：M1–M3 约 1.5–2 周（loop 本身小，成本在工具鲁棒性与 provider 兼容），
  M4–M5 约 1 周。

已决项 `--session-dir` 的现行语义（落 `session.jsonl` 镜像、不提供 resume 入口）已并入
现行文档 §6。
