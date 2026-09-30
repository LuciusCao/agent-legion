/**
 * 草稿自动校验（#804 定案 + codex 轮 3 P2 + 轮 4 P1-1）：当前草稿内容
 * 「已落盘且尚未校验」时静默校验——不开校验报告抽屉、不弹 toast，结果经
 * reportSilent 写入 validation state，驱动左岛状态 chip（未发布变更 →
 * 校验中… → ✓ 校验通过 / ✗ 校验失败）与发布门控（canPublish 要求当前
 * YAML 明确校验通过）。
 * 触发条件（全部满足）：canSubmit（有未发布变更且非只读）+ 保存状态机
 * settled（saved，或 idle 且 savedAt 非空——hydrate 恢复服务端草稿不
 * 产生 saved 边沿，这是轮 3 P2 的发布门控洞）+ 无在途校验 + 当前内容
 * 无校验结果。debounce 窗口内（pending/saving）不触发，按未校验处理。
 * 轮 4 P1-1：结构失败（valid:false）直接终态「校验失败」不重试；传输
 * 失败（reject）由 validateDraftWithRetry 退避自动重试，耗尽才写终态
 * 「校验失败：…」。终态后的恢复路径：下一次落盘重新触发——终态
 * message 随 definitionYaml 变化被 useValidationFeedback 作废，不闭锁。
 * 迟到结果按捕获的 yaml 比对丢弃（DraftValidationStaleError 静默吞）。
 * 从 useWorkflowStudioActions 拆出保体积预算；重试循环在
 * draftAutoValidationRunner.ts。
 */
import { useEffect, useRef } from 'react'
import {
  DraftValidationStaleError,
  validateDraftWithRetry,
} from './draftAutoValidationRunner'
import type { DraftSaveState } from './draftSaveTypes'

type Params = {
  workspaceId: string | undefined
  /** 草稿保存状态机（status + savedAt：hydrate 后 idle+savedAt 非空）。 */
  saveState: DraftSaveState
  /** 与 useWorkflowStudioActions.canSubmit 同口径：有未发布变更且可提交。 */
  canSubmit: boolean
  definitionYaml: string
  /** 当前内容的校验结果（'' = 未校验/已作废），来自 useValidationFeedback。 */
  validationMessage: string
  /** 有校验在途（actionState === 'validating'）。 */
  validating: boolean
  reportSilent: (errors: string[], message: string) => void
  setValidating: (on: boolean) => void
}

export function useDraftAutoValidation({
  workspaceId,
  saveState,
  canSubmit,
  definitionYaml,
  validationMessage,
  validating,
  reportSilent,
  setValidating,
}: Params) {
  // 最新 草稿+workspace 镜像：迟到结果与之比对，不一致即作废（旧校验不得
  // 覆盖新编辑/新 workspace——轮 7 P2：同 YAML 的跨 workspace 复用页面，
  // 在途请求迟到写入必须按 workspaceId 一并作废）。
  const contentRef = useRef({ ws: workspaceId, yaml: definitionYaml })
  useEffect(() => {
    contentRef.current = { ws: workspaceId, yaml: definitionYaml }
  })
  // 在途运行序号：连续校验时先到期的旧运行不得清掉新运行的 validating
  // （finally 只认最新一次）。
  const runIdRef = useRef(0)

  useEffect(() => {
    const settled =
      saveState.status === 'saved' ||
      (saveState.status === 'idle' && saveState.savedAt !== null)
    if (!workspaceId || !canSubmit || !settled) return
    if (validating || validationMessage !== '') return
    const yaml = contentRef.current.yaml
    const ws = workspaceId
    const runId = (runIdRef.current += 1)
    setValidating(true)
    validateDraftWithRetry(
      ws,
      yaml,
      () => contentRef.current.ws !== ws || contentRef.current.yaml !== yaml
    )
      .then((result) => {
        reportSilent(result.errors, result.valid ? '校验通过' : '校验失败')
      })
      .catch((e: unknown) => {
        if (e instanceof DraftValidationStaleError) return
        reportSilent(
          [],
          `校验失败：${(e instanceof Error && e.message) || '网络错误'}`
        )
      })
      .finally(() => {
        if (runIdRef.current === runId) setValidating(false)
      })
  }, [
    workspaceId,
    saveState,
    canSubmit,
    validationMessage,
    validating,
    reportSilent,
    setValidating,
  ])
}
