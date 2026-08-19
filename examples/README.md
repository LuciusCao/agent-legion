# 示例 workflow 资源：education_video_problems_generation

本目录是开源仓库自带的极简示例 workflow（教学视频脚本 + 题目生成）的全部
业务侧资源，与生产业务无关、可直接公开：

- `education-video-problems-generation/`：10 个通用中小学数学知识点
  markdown，是示例 intake 节点（`intake_knowledge_points`）的输入素材。
  每个文件名主干（如 `fraction-addition-subtraction`）即 intake 的输入值。
- `skills/`：4 个示例 agent skill（`write-script` / `review-script` /
  `generate-questions` / `review-questions`），随仓库版本化。运行
  `make import-demo` 把它们导入本机 skill 源目录
  `~/.agents/skills/agent-legion/education-video-problems-generation/`
  并逐个 `git init` + 打 tag `v1.0.0`（幂等，不覆盖已有改动）。

示例 DAG（`server/app/workflows/builtin_demo.py` 的
`education_video_problems_generation`；`server/app/workflows/builtin.py`
仅 re-export）：
intake_knowledge_points → write_script → review_script →
generate_questions → review_questions → publish_content（模拟入库，
不发网络请求）。

跑通示例还需要：后端已启动、`make import-demo` 已执行、skill lock 已刷新
（`make skills-lock`）、workspace 绑定该 workflow 并配置了 agent 执行所需的
模型默认值（workspace Settings 的 `default_agent_provider` /
`default_agent_model`）。

两个 code 节点（intake/publish）的出厂代码随后端启动自动发布为全局
node_code 版本（seed-if-absent，源自 `workflow_nodes/` 的 git 评审文件）；
它们与其他 code 节点一样在 velites 沙箱内执行（#96），因此运行示例的
Host 需要 velites 二进制在 PATH（或用 `scripts/ensure-velites.sh` 安装到
`data/bin/`）且 macOS `sandbox-exec` / Linux `bwrap` 可用。
