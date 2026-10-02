# Worker 控制台 PR 栈

GitHub 原生 base branch 就表达依赖，不需要额外复制 PR 或安装工具：

| PR | head | base | 本层职责 |
| --- | --- | --- | --- |
| #762 | `feat/worker-console-entry` | `release/0.7.14` | 部署地址、入口与接入说明 |
| #763 | `feat/worker-console-self-report` | `feat/worker-console-entry` | Worker 自报地址与逐行入口 |
| #765 | `feat/worker-claim-state-loop` | `feat/worker-console-self-report` | 领取状态与执行准备闭环 |

## 修改与复审

1. 共享缺陷先在最底层受影响 PR 修复。明确输入来源、优先级、合法值、失败行为和消费边界，补可复现反例及关联场景。
2. 底层完成实现与独立复审后，再把已稳定的 head 正常合并到直接子分支，依次更新上层；不在每个中间试验提交后反复同步整栈。
3. 每层 review 使用它实际的 base 比较，只审本层增量；本地检查仍按仓库门禁执行。每次 head 变化后，CI 证据必须对应新 SHA。
4. 当前 Quality Gate 的 PR 触发器只覆盖主干和 release 分支。#763/#765 以功能分支为 base 时，推送稳定版本后手动触发 `quality-gate.yml`，确认 run 的 head SHA 与 PR 一致。

## 从底向上合并

此栈优先使用 GitHub **Create a merge commit**，保留已被子分支继承的提交祖先。先合并 #762，再把 #763 的 base 改为 `release/0.7.14`，确认 diff 只剩本层、重验 CI 后合并；随后同样处理 #765。每次合并仍需用户授权及最新 `quality-gate` 通过。

若 owner 选择 squash/rebase 合并，不能只修改子 PR 的 base：被继承的父提交不再是 release 的祖先，必须先核对 merge-base 和 diff，再明确重放仅属于子层的提交。涉及改写已推送历史时单独协调，不能把重复父改动混入子 PR。

#764 与 #766 是独立的 release PR；它们的合并不改变以上 base 关系。PR 描述标注栈位置、直接依赖和当前验证 SHA，避免把旧版本的绿色 CI 当作新 head 的合并凭证。
