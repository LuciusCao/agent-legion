# M44 实测验收材料：Studio 任务中心 + 大批量异步提交（#1157 Phase 3）

对照权威原型 `~/Desktop/0.8.1-ui-acceptance/m44-async-submit/prototype.html`
四场景的**真实实现**实测（非 mock）：wt-44 起真实后端（:8944，schema
v100）+ 真实前端 build，Playwright 操作 + 截图。截图为本目录 `shots/`。

## 操作路径（与截图一一对应）

1. **① 提交入口链路**（`m1-*`）：任务列表页（AppBar「任务」入口带徽标）
   →「添加」→ 粘贴 ID 页签粘贴 1,200 个外部 ID（> 1,000 阈值）→ 对话框
   蓝色提示「转为后台创建」（`m1-dialog-bulk-hint.png`）→「创建运行」→
   `POST /runs mode=async` 秒级 202 → 对话框即关、左下受理通知带
   「查看进度 →」、AppBar 徽标数字即时+1（纯数字无动效，用户验收裁决；
   `m1-accepted-toast-badge.png`）
   → 点「查看进度 →」跳任务中心，新 run 以「排队中」卡片出现
   （`m1-task-center-after-accept.png`）。本次受理的 1,200 个 job 随后由
   feeder 真实建成并出现在任务列表（`m1-joblist-badge.png`，重拍于
   feeder 完成后，列表横幅「有 1200 个任务在等待中」）。
2. **② 任务中心**（`m2-task-center-1440.png` / `-1728.png`）：分区
   「进行中与排队」（进行中卡片：进度条、已处理/新建/跳过、耗时与预计
   剩余、暂停/取消；已暂停卡片：继续/取消）+「已结束」（完成含跳过数、
   部分完成含 chunk 错误块、失败含原因、已取消；终态卡片有「查看任务 /
   归档」；底部瀑布流加载更早）。1440 → 3 列、1728 → 4 列响应式网格。
3. **③ 已归档**（`m3-archived.png`）：只读视图（无操作按钮），卡片底部
   标注来源——「由 admin 手动归档」/「自动归档（结束超过 7 天）」，
   顶部说明条含 90 天留存规则。
4. **④ 空态**（`m4-empty.png`）：无批量操作记录的引导文案（何物会出现
   在这里 + 自动归档预告），徽标计数为 0 时不显示。

## 环境与方法（可复现）

- 后端：`uv run uvicorn server.app.main:create_prod_app --factory --port 8944`
  （`AGENT_LEGION_DATABASE_URL=postgresql://127.0.0.1:5432/agent_legion_wt_44`
  - `AGENT_LEGION_VAULT_MASTER_KEY`），`frontend/npm run build` 后由后端
    SPA 挂载直接服务。
- 数据：demo workspace 各状态 run_batches 种子（进行中/已暂停/完成/部分
  完成/失败/取消/手动归档/自动归档）+ 真实 `mode=async` 提交 1,200 个
  ref 条目（连接 `cms-demo`，feeder 真实建成 1,200 个 job）。
- 截图：Playwright（chromium），1440×900 与 1728×900 两档视口。
  种子/截图脚本为一次性工具，未入库。

## 与原型的偏差说明（实现侧取舍）

- 「已归档 (N) →」入口不带归档总数（列表契约不返回归档总计数，避免
  额外 count 查询；原型计数为示意）。
- 手动归档的来源标注为「由 {提交者} 手动归档」：`run_batches` 只记
  `created_by`（提交者），不记归档操作者；单操作者场景两者一致。
- 终态卡片不提供「重试」按钮：本切片端点面未含 retry（失败/部分失败
  的重提语义 = 重新提交同一批 items，确定性 run id + dedup 自愈，见
  docs/architecture/runs-async-mode.md §4）。
- 失败卡片的批量级 `error_message` 与 chunk 错误原样呈现（ feeder 记录
  的第一条 chunk 错误或 batch 错误）。
