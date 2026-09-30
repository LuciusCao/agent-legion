/**
 * 草稿自动校验（#804 定案）：草稿保存成功（draftSave.status 进入 saved）
 * 后静默校验当前草稿——不开校验报告抽屉、不弹 toast，结果经 reportSilent
 * 写入 validation state，驱动左岛状态 chip（未发布变更 → 校验中… →
 * ✓ 校验通过 / ✗ 校验失败）与发布按钮的禁用门控。草稿再编辑后旧结果
 * 作废（useValidationFeedback 随 definitionYaml 清空，chip 回「未发布
 * 变更」）；校验在途期间草稿变化时，迟到的结果按捕获的 yaml 比对丢弃。
 * 干净态（canSubmit=false：无未发布变更/只读/空草稿）不触发。
 * 从 useWorkflowStudioActions 拆出保体积预算。
 */
import { useEffect, useRef } from 'react'
import { validateWorkflowDraft } from '../../../api'
import type { DraftSaveStatus } from './draftSaveTypes'

type Params = {
  workspaceId: string | undefined
  /** 草稿保存状态机的当前状态（saved = 一次 PUT 成功落盘）。 */
  saveStatus: DraftSaveStatus
  /** 与 useWorkflowStudioActions.canSubmit 同口径：有未发布变更且可提交。 */
  canSubmit: boolean
  definitionYaml: string
  reportSilent: (errors: string[], message: string) => void
  setValidating: (on: boolean) => void
}

export function useDraftAutoValidation({
  workspaceId,
  saveStatus,
  canSubmit,
  definitionYaml,
  reportSilent,
  setValidating,
}: Params) {
  // 最新草稿镜像：迟到结果与之比对，不一致即作废（旧校验不得覆盖新编辑）。
  const yamlRef = useRef(definitionYaml)
  useEffect(() => {
    yamlRef.current = definitionYaml
  })
  const prevSaveStatus = useRef(saveStatus)
  // 在途运行序号：连续保存触发两次校验时，先到期的旧运行不得清掉新运行
  // 的 validating（finally 只认最新一次）。
  const runIdRef = useRef(0)

  useEffect(() => {
    const prev = prevSaveStatus.current
    prevSaveStatus.current = saveStatus
    // 只在「进入 saved」的边沿触发：saved 常驻期间（hydrate/重复渲染）不重跑。
    if (saveStatus !== 'saved' || prev === 'saved') return
    if (!workspaceId || !canSubmit) return
    const yaml = yamlRef.current
    const runId = (runIdRef.current += 1)
    setValidating(true)
    validateWorkflowDraft(workspaceId, yaml)
      .then((result) => {
        if (yamlRef.current !== yaml) return
        reportSilent(result.errors, result.valid ? '校验通过' : '校验失败')
      })
      .catch((e: unknown) => {
        if (yamlRef.current !== yaml) return
        reportSilent(
          [],
          `校验失败：${(e instanceof Error && e.message) || '网络错误'}`
        )
      })
      .finally(() => {
        if (runIdRef.current === runId) setValidating(false)
      })
  }, [workspaceId, saveStatus, canSubmit, reportSilent, setValidating])
}
