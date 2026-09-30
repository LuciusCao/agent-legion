# Studio 草稿-校验-发布契约

前端：`frontend/src/features/workflowStudio/`。本文以代码为准钉住 studio
草稿编辑的三台协作状态机与它们的组合 invariant；改这块代码前对照
invariant 表。

## 三台状态机

### 1. 草稿保存机（draftSaveController.ts + draftSaveConflict.ts）

状态：`status ∈ {idle, pending, saving, saved, error}` × `conflict ∈ {false,
true}`；`loadError`（GET 草稿失败）由组合层（useWorkflowDraftPersistence）
并入展示，不进状态机。`savedAt`/`lastPersistedAt` 是 CAS 基线时间戳。

| 事件 | 迁移 |
|------|------|
| 编辑（已 hydrated） | 空内容 → 不变；等于已持久化且无在途 → 收回 pending/error（revert）；否则 → `pending`（arm 800ms debounce；conflict 时挂起不 arm） |
| debounce 到期 / flushNow | `pending` → `saving` → 发 PUT（携带 CAS 基线） |
| PUT 成功（current） | → `saved`，推进 `lastPersisted` + `lastPersistedAt` |
| PUT 成功（迟到/作废） | 状态不变，仅推进 CAS 基线（I4） |
| PUT 409 | → `error` + `conflict=true`：清计时器与 pendingSave，基线推进到冲突响应的 updated_at，不自动重试 |
| PUT 其它失败 | → `error`，指数退避重试 ≤2 次（2s/4s），耗尽停 `error` |
| keep-mine（resolveConflict(true)） | 清 conflict；有 pending 补发；无 pending 按当前画布内容**强制写回**（forceSave 绕开 schedule 去重——内容等于已持久化值时去重会吞掉补救，警示消失而内容从未写回） |
| 采用服务端（adoptServerDraft → hydrate）/ resolveConflict(false) | 清 conflict，status 收敛 saved/idle（不留假 error） |
| pagehide / visibilitychange→hidden | flushNow（pagehide 带 keepalive）；conflict 态有意 no-op 且 resolve `ok:false`（等待方据此中止，与 draftSaveQueue 的 drain 同语义） |
| 服务端草稿前进（turn-end 重取，serverDraftReapply.ts） | 用户未碰 → apply（写画布+推进基线）；已碰 → conflict；own-save 回显（服务端==画布）→ 静默 apply 不升冲突 |

### 2. 自动校验机（useDraftAutoValidation.ts + draftAutoValidationRunner.ts）

触发：保存机 settled（`saved`，或 hydrate 形态的 `idle`+savedAt 非空）∧
canSubmit（有未发布变更且非只读）∧ 无在途校验 ∧ 当前内容无校验结果。
debounce 窗口内（pending/saving）按未校验处理，不触发。

| 事件 | 迁移 |
|------|------|
| 触发条件满足 | → 校验中（validating=true），静默 POST validate |
| resolve valid | → ✓ 校验通过（chip 绿，发布解禁） |
| resolve valid:false | → ✗ 校验失败（结构失败，不重试；chip 红 + 抽屉详情） |
| reject（网络/5xx） | 传输失败：runner 内退避重试 2s/4s/8s ≤3 次；耗尽 → 「校验失败：…」终态 |
| 编辑 / workspace 切换 | 结果作废回未校验（message 清空）；迟到结果按 workspaceId+YAML 双重比对丢弃 |
| 传输失败终态后的恢复 | 抽屉「重试校验」（清空即重跑）或再编辑落盘 |

### 3. 发布动作机（useWorkflowStudioActions.ts）

在途维度 `publishing` 与 `validating` 独立（互不覆盖）。`canPublish =
canSubmit ∧ compare 无阻断且 ready ∧ 有变更 ∧ 校验通过 ∧ !conflict`。

| 事件 | 迁移 |
|------|------|
| requestPublish（canPublish 为真） | 快照当时 YAML（reviewYaml）→ 开确认框 |
| 确认框打开期间 YAML 变 / conflict 进入 | `reviewStale` → 禁确认 + 提示重审；onConfirm 提交前兜底重查 `reviewStale \|\| !canPublish` |
| 确认 → publishDraft | publishing=true → POST publish；成功：markDraftPublished → reload → toast 保存成功；服务端校验未过：错误归校验通道（chip ✗）+ toast；网络 reject：纯 toast（校验通道不染，可重发） |

## Invariant 表

| # | 语义 | 违反后果 | 钉住它的测试 |
|---|------|----------|--------------|
| I1 | conflict 是全局唯一需用户决策态：conflict ⇒ autosave 挂起、flushNow 不发 PUT 且 resolve ok:false、校验机冻结、警示在页签行/抽屉内恒可见可操作、canPublish=false | 隐式覆盖 Agent 版本 / 审 A 发 B / 编辑静默丢失 | `useWorkflowDraftPersistence.cas.test.ts`、`WorkflowStudioNarrowAlertBadge.test.tsx`、`WorkflowStudioLayout.test.tsx`、`WorkflowStudioDraftSaveControl.test.tsx`、`useWorkflowStudioActions.test.ts` |
| I2 | 任何非冲突保存失败终态都有恢复路径（再编辑 / flush / 显式「重试保存」） | 草稿永久停内存，离页丢内容 | `useWorkflowDraftPersistence.test.ts`、`WorkflowStudioDraftSaveControl.test.tsx` |
| I3 | 发布的 YAML == 用户审阅的 YAML == 当前 workspace 校验通过的 YAML（reviewYaml 快照 + conflict 失效 + workspaceId 绑定三维） | 发布内容与审阅 diff 不一致、跨 workspace 误放 | `useWorkflowStudioActions.test.ts`、`WorkflowPublishReviewDialog.test.tsx` |
| I4 | 作废（迟到）响应不构成失败信号，但推进 CAS 基线 | 连续编辑误报冲突 / 假失败 | `useWorkflowDraftPersistence.cas.test.ts` |
| I5 | 用户编辑永不静默丢弃（pagehide keepalive flush + visibilitychange flush + beforeunload 确认）。已知权衡：conflict 下 pagehide flush 有意 no-op（防静默覆盖 Agent 版本），移动端 beforeunload 不可靠 | 离页丢编辑 | `useWorkflowDraftPersistence.test.ts`（flushNow/unload 用例） |
| I6 | 校验结果必然属于当前内容（编辑即作废、迟到丢弃、workspaceId 绑定） | 陈旧/跨工作区结果驱动发布门控 | `useWorkflowStudioActions.test.ts` |
| I7 | 发布与校验的在途状态互不覆盖（publishing/validating 独立维度） | 发布在途时校验落定误解禁发布入口（重复发布） | `useWorkflowStudioActions.test.ts`（交错序列） |
| I8 | 非冲突的失败终态（校验传输失败、compare 失败）都有可见原因 + 显式重试入口 | 隐形死锁：发布被禁且界面零提示 | `useWorkflowStudioActions.test.ts`、`useWorkflowStudio.test.ts`、`WorkflowStudioCanvasSourceBadge.test.tsx` |

## 变更纪律

- 改本目录这块代码前对照 invariant 表；新增状态/事件必须同步更新迁移表。
- 每条 invariant 至少一个 revert-即红测试钉住（摘掉实现即红的回归）。
- 失败路径分两族：结构失败（内容问题，修复后重发，不自动重试）与传输
  失败（网络/5xx，退避自动重试 + 终态显式重试入口）——新增失败态先归类。

## 历史教训

#633 时代草稿保存是 incremental 堆叠（debounce、重试、CAS、冲突逐层
叠加），组合面无人建模；#804 期间 codex 多轮复审 + 对抗式审查发现的
P1 半数是历史弱点被新发布门控（校验绑定发布）武器化后的爆发。本契约
是把组合空间一次性钉死的产物：三台状态机的迁移表是单一事实来源。
