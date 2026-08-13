# Workflow Studio 演进：Agent  authoring + 可视化调优（设计）

状态：方向已定案（2026-08-12 讨论），阶段 0 待实施；dry-run 待设计（§6）
日期：2026-08-12
关联：Issue #30/#82、`node-sdk-and-worker-execution-design.md`、
`custom-workflow-nodes-design.md`、EXEC-CODE-001/002/003

## 1. 定位

- 新 workflow 的作者是 **Agent**，不是人。Studio 的定位是**可视化 + 调优 +
  发布台**：agent 产草稿，人在 Studio 审查 diff、微调、发布、跑批、观察。
- Studio 将内置一个对话式 agent（平台自身的 pi/velites agent + 一组平台工具），
  覆盖「生成草稿」与「节点调优」两个场景。
- 不建 DAG 画布拖拽搭建器：agent 生成的 workflow 比人手写 YAML 靠谱，人要做的
  是审和调，不是从零搭。

## 2. 新用户旅程地图与当前断点

| 站 | 目标态 | 当前断点 |
|----|--------|----------|
| 1. 描述需求 | Studio 内置 agent 对话 | 不存在 |
| 2. AI 生成草稿 | workflow DAG + capability + 节点代码 + agent 定义一把产出 | workflow 存不进（catalog 硬编码 `server/app/workflows/builtin.py`）；code capability 强制 repo 文件（`server/app/executors/code_config.py:75` `is_file()` 校验，与自定义代码存 DB 的模型矛盾）；skill 只能外部仓库 |
| 3. 可视化审查 | DAG 上看结构、看每节点绑定的 agent/executor | 画布只读；绑定编辑在 Settings 页（`SettingsPage.tsx` ExecutorAllocation/BindingSection），agent/executor 管理是孤立入口，关系链断裂 |
| 4. 细节打磨 | 改 config/代码，agent 参与调优 | code 节点只能 fork 内置代码起稿（`WorkflowNodeCodeSection.tsx:158`），无模板、无 agent 入口 |
| 5. 看到效果 | dry-run 单节点验证 | 完全没有；改完只能跑整个 batch |
| 6. 发布运行 | 发布流 + 校验 + 触发 | 现状最完整的一站（发布流、PublishReviewDialog、job-batches 均在） |

## 3. 阶段划分

- **阶段 0 · 地基解阻**（便宜、无争议、一切的前置）：
  1. workflow catalog 去硬编码：`builtin.py` 注册表 DB 化/可注册，新 workflow
     key 不再依赖改 repo（agent 生成 workflow 的存储前置）；
  2. code capability 的 `path` 校验松绑：自定义代码存 DB，不应强制 repo
     占位文件；
  3. 节点代码「从模板新建」入口：模板由后端下发（单源，随 SDK 演进），
     替代 fork 200 行内置代码起稿；
  4. executor 定义发布后热生效，不再要求重启后端（调度 registry 目前仅启动
     时 hydrate，`ExecutorsPanel.tsx:76-79`）；
  5. 校验前置：binding/配置错误在编辑时/validate 时报全，不攒到 publish
     （`services/workflow_drafts.py:62-67`）。
- **阶段 1 · 关系可视化**：DAG 画布显示并就地编辑节点绑定（agent/executor）；
  agent/executor/节点代码管理入口收进 Studio 或从 DAG 节点一键跳转。
- **阶段 2 · dry-run**：单节点试跑（详见 §6，待设计）。是打磨闭环与 code 节点
  agent 调优的前提。
- **阶段 3 · Studio 内置 agent**：对话生成草稿 + 节点调优。配工具面：workflow
  草稿/校验/发布 API、节点代码 API、dry-run API（阶段 2 后）。**agent 只能产
  草稿 + validate，发布永远由人点**（已定案，§4）；发布流与
  PublishReviewDialog 正好卡在这个边界上。
- **阶段 4 · skill**：已缩减——skill 维持外部仓库 + git 评审链（已定案，§4），
  Studio 不做 skill 创建，仅做展示/选择。

## 4. 已定案决策（2026-08-12）

1. **新 workflow 作者是 Agent**，Studio = 可视化 + 调优 + 发布台；不做 no-code
   画布搭建器。
2. **skill 存储不变**：外部 git 仓库 + tag + relock 评审链保留；Studio 不创建
   skill（用户内容不直接进入 agent 执行面，prompt 注入面的评审链不绕过）。
3. **Studio 内置 agent 只产草稿**：可调用草稿/校验类 API；发布、回滚等生效动作
   必须人工在 Studio 确认（复用现有发布流与 diff 审查）。

## 5. 现状关键事实（盘点 2026-08-12）

- workflow 定义存 `workflow_revisions.definition_json`，active revision 递增；
  publish 快照 `node_code_pins`。Studio 编辑的 YAML 即该定义的完整序列化。
- Agent/Executor/节点代码存 `versioned_entities`，draft→published→archived。
- Agent 定义已可纯 UI 新建（AgentsPanel/AgentEditor）；Executor 新建受
  `is_file()` 校验限制（§3 阶段 0-2）。
- 触发运行已有：`POST /workspaces/{id}/job-batches`（AddDialog）。

## 6. 待设计：dry-run（阶段 2，未拍板）

问题清单：

1. 执行位置：Host 本地 executor 还是 Worker？（与批次 2 路由的关系）
2. 产出隔离：dry-run 的 artifact 必须与正式 job 数据隔离，不进正常 artifact
   命名空间、不影响 checkpoint；
3. 输入来源：上游节点历史 artifact 样本 vs 用户手填 vs 两者；
4. agent 节点 dry-run 烧真实模型 token：配额/确认机制；
5. 与发布流的关系：dry-run 跑的是草稿态代码/config（未发布）还是已发布版本？
   （调优闭环要求草稿态可跑。）

## 7. Quality Impact

- **测试**：阶段 0 各项需配套测试——catalog DB 化的迁移与回退、`path` 校验
  松绑后的 executor 发布校验、模板下发契约、executor 热生效的调度一致性、
  validate 前置的错误覆盖面。dry-run 落地时需补产出隔离与权限的契约测试。
- **架构治理**：catalog DB 化涉及 workflow 定义的注册边界，落地前读
  `config/architecture/` 并评估是否新增 invariant；Studio agent 工具面不得
  绕过发布流（§4-3），需在 API 层强制（agent 身份无 publish 权限），而非
  仅靠前端隐藏。
- **安全**：Studio agent 工具面的权限边界（草稿可写、生效不可写）属安全
  敏感面，落地时需在证据矩阵登记。
