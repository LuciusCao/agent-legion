# Agent Legion

*[English README](README_EN.md)*

Agent Legion 是一个自托管控制台，把 AI agent 变成内容生产线的受管工人。
你把工作流定义成业务**能力**（capability）组成的 DAG——「批量 intake」
「写脚本」「评审」「生成练习题」——Agent Legion 负责把每个节点调度到你
的机器上执行，并提供一个实时控制台，让你观察进度、重跑失败节点、收集
打包好的产物。

它面向的不是一次性的 LLM 对话，而是可重复、可审计的生产过程。

## 你能得到什么

- **业务方能看懂的工作流。** 节点只声明做什么（能力 + 输入/输出产物），
  不声明怎么跑。在内置的 Studio 里可视化编排并发布；每个 workspace
  拥有自己的 DAG 与版本历史。
- **批量进，结果出。** 一次 API 调用提交一批工作项，每项成为一个 job
  流过 DAG。支持单节点重跑、跑到指定节点、从暂停处继续——下游过期
  状态自动跟踪。条目类型除了单文件材料与外部引用，还有 **bundle 文件夹
  条目**——整个文件夹作为一个条目提交（manifest 引用式）。
- **实时运维控制台。** React SPA：实时 DAG 视图、SSE 仪表盘事件、
  WebSocket agent 状态、运行日志、产物、token 用量统计、按失败类别
  批量重跑。
- **加机器就能扩容。** 远程 Agent Worker 经 HTTP 注册、领取执行、上传
  产物。容量按池分配并强制隔离，廉价的 code 任务洪水永远不会饿死你的
  agent 执行。
- **可复现、可审计。** 外部 skill 就是普通 git 仓库，按锁定的 commit
  固定版本；每次节点执行都留下完整痕迹（prompt、事件流、stderr），
  事后可随时复查。
- **秘密妥善处理。** workspace 与实例级凭据经 Fernet 加密进 vault；
  配置与快照只携带引用，永不落明文。
- **默认多用户。** Cookie 会话 + CSRF 防护、admin 用户管理、按
  workspace 的 editor/viewer 成员权限。

## Quick Start

### 前置要求

- macOS + Homebrew：`make install` 会自动检测并安装缺失的依赖
  （Python 3.11+、Node 18+、PostgreSQL 17、[`uv`](https://docs.astral.sh/uv/)、
  Rust 工具链、Docker）。其他平台请先手动安装这些依赖，`make install`
  会逐项检测并给出指引。
- 一个 LLM provider 供 agent 节点使用（任何 OpenAI 兼容端点均可；
  demo workflow 需要一个）。

### 1. 克隆与安装

```bash
git clone https://github.com/LuciusCao/agent-legion.git
cd agent-legion
make install    # 装依赖、uv sync、建开发库 agent_legion_dev、生成 .env（含本地
                # RustFS 随机凭据）、构建 velites、装前端依赖、种子 worker
                # 配置——幂等，可重跑
```

开发库用派生名 `agent_legion_dev` 而非裸名 `agent_legion`：裸名是共享/prod
库，`init_db` 在没有 `AGENT_LEGION_ALLOW_SHARED_DB_SCHEMA=1` 时会拒绝迁移它
（共享库 schema 守卫）。

对象存储默认使用本地 **RustFS**（`make dev-up` 自动启动容器并建好 bucket，
凭据由 `make install` 生成进 `.env`），开箱即用；要切换到云端 S3（AWS 或
其他兼容服务），只需在 `.env` 里改 `AGENT_LEGION_S3_ENDPOINT` / 凭据 /
`AGENT_LEGION_S3_BUCKET` 三样，本地 RustFS 会被自动跳过（详见
[docs/materials-storage-deployment.md](docs/materials-storage-deployment.md)）。

docker 不可用（未安装或未启动）时 `make dev-up` 会跳过本地 RustFS：
示例材料播种同步跳过、材料相关 API 降级返回 503，其余功能不受影响；
docker 就绪后重跑 `make dev-up` 即可补齐。

### 2. 启动

```bash
make dev-up         # 本地 RustFS + 后端 :8001 + 控制台 :5174 + worker :8789——幂等
make dev-status     # 查看各组件状态与 URL
make dev-down       # 全部停止
```

打开 http://127.0.0.1:5174——首次访问会跳转到 `/setup` 创建 admin 用户。
worker 按设计默认关闭任务领取，到 worker 控制台 http://127.0.0.1:8789
打开。

worker 注册不再使用全局 token：启动后在 Host Web UI 的
「设置 → Worker Token」为 workspace 签发 scoped token，到 Worker 控制台
（`http://127.0.0.1:8789`）的「Workspace 访问」区块粘贴添加即可——token
随时可以补，无需重启后端（详见
[docs/agent-worker-deployment.md](docs/agent-worker-deployment.md)）。

### 3. 跑通 demo workflow

仓库自带一个极简 demo workflow
**`education_video_problems_generation`**：`examples/` 下 10 个通用中小学
数学知识点 markdown 随 demo workspace 播种为示例材料，每个材料展开为一个
job——撰写教学视频脚本、评审、生成 5 道练习题、评审，最后模拟发布
（不发网络请求）。

```bash
make import-demo      # 安装并锁定 demo skills；不存在时创建并 seed demo workspace
```

然后在控制台里：

1. 打开命令输出中的 demo workspace（重复运行不会创建第二个）。
2. 在 Studio 里配置 agent 执行：打开 workflow，在顶层 `execution:` 填入你的
   LLM 端点提供的 provider/model（一处即覆盖全部 agent 节点，也可逐节点
   `execution.*` 覆盖；输入框会按节点 Agent 的 runtime 给出在线 Worker
   上报的可用 provider/model 选项，也可手输）。
3. 打开 workspace 的自动调度，并在 Worker 控制台打开 claim。
4. 提交一批任务：在 workspace 里「添加条目」对话框上传知识点 markdown，
   或在面板中勾选已播种的示例材料，确认后创建运行——每个材料一个
   job。（「粘贴 ID」面板是 **ref 条目**：需先在 admin 配置外部服务连接，
   粘贴该连接下的外部 ID。）
5. 看 DAG 实时点亮；每个节点完成后可以查看它的完整执行痕迹与产物。

### 下一步

- **在 Studio 里编排自己的工作流**（草稿 → 发布）并挂上自己的
  skill——demo 的接线方式见 `examples/README.md`。
- **加更多机器当 worker**：
  [docs/agent-worker-deployment.md](docs/agent-worker-deployment.md)。
- **生产部署**（Docker stack、PostgreSQL）：
  [docs/architecture/deployment.md](docs/architecture/deployment.md) 与
  [docs/postgresql-runbook.md](docs/postgresql-runbook.md)。

## 文档

| 我想… | 看这里 |
|-------|--------|
| 把系统跑起来 / 跑 demo | 本文件 + `examples/README.md` |
| 运维（部署、worker、远程执行） | [docs/](docs/README.md)——部署、worker 与 runbook 文档 |
| 材料存储（RustFS/S3） | [docs/materials-storage-deployment.md](docs/materials-storage-deployment.md) |
| 理解原理（架构、配置参考、runtime） | [docs/architecture/](docs/architecture/README.md) |
| 贡献代码 | [CONTRIBUTING.md](CONTRIBUTING.md) 与 [AGENTS.md](AGENTS.md) |
| 跟踪变更 | [CHANGELOG.md](CHANGELOG.md) |

## 许可证

[MIT](LICENSE) © Lucius Cao
