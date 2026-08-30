# 示例 workflow 资源：education_video_problems_generation

本目录是开源仓库自带的极简示例 workflow（教学视频脚本 + 题目生成）的全部
业务侧资源，与生产业务无关、可直接公开：

- `education-video-problems-generation/`：10 个通用中小学数学知识点
  markdown，是示例 intake 节点（`intake_knowledge_points`）的输入素材。
  绑定示例 workflow 的 workspace 会把这些文件播种为**示例材料**
  （seed-if-absent，需实例配置 `AGENT_LEGION_S3_*` 对象存储；未配置则
  跳过并记 warning，可后续手动上传），intake 节点从 job 的材料输入
  （`ctx.material`）读取 markdown——每个材料一个 job。
- `skills/`：4 个示例 agent skill（`write-script` / `review-script` /
  `generate-questions` / `review-questions`），随仓库版本化。运行
  `make import-demo` 把它们导入本机 skill 源目录
  `~/.agents/skills/agent-legion/education-video-problems-generation/`
  并逐个 `git init` + 打 tag `v1.0.0`，随后写入 skill lock，并在尚无
  demo workspace 时创建和 seed 一个（幂等，不覆盖已有改动）。

示例 DAG（`server/app/workflows/builtin_demo.py` 的
`education_video_problems_generation`；`server/app/workflows/builtin.py`
只是装配入口，把 demo 定义挂进 `BUILTIN_WORKFLOW_DEFINITIONS`）：
intake_knowledge_points → write_script → review_script →
generate_questions → review_questions → publish_content（模拟入库，
不发网络请求）。

跑通示例还需要：`make import-demo` 已执行，并在 Studio 里配置 agent 执行的
provider/model（在 workflow 顶层 `execution:` 配一处即可，也可逐节点
`execution.*` 覆盖；Studio 会按节点 Agent 的 runtime 给出在线 Worker 上报的
可用 provider/model 选项），同时开启 workspace 自动调度和 Worker claim。

两个 code 节点（intake/publish）的出厂代码在绑定示例 workflow 时发布为
workspace 作用域 node_code 版本（seed-if-absent，源自 `workflow_nodes/` 的
git 评审文件）；它们与其他 code 节点一样在 velites 沙箱内执行（#96），
因此运行示例的 Host 需要 velites 二进制在 PATH（或用
`scripts/ensure-velites.sh` 安装到 `data/bin/`）且 macOS `sandbox-exec` /
Linux `bwrap` 可用。
