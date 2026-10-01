/**
 * 预览面板治理动作 hook（#796 验收返工，从 PreviewPanelSection 抽出保体积
 * 预算）：发布/恢复默认（归档）是头部治理区的人工动作——agent 只写草稿
 * （reject_studio_agent_scope 在后端钉死），失败原因收进 actionError 由
 * 治理区展示（role=alert），不抛全局 toast。
 * 状态按 workspaceId 键控（codex P2）：react-router 复用实例跨 workspace
 * 导航时，A 的 actionError/pending 不得泄漏到 B；A 的迟到结果写入时携带
 * 发起时的 workspaceId 快照，渲染期派生过滤（迟到响应不进 B 的头部）。
 */
import { useState } from 'react'
import {
  useArchivePreviewPanel,
  usePublishPreviewPanel,
} from './usePreviewPanel'

type Action = 'publish' | 'archive'
type Scoped = { workspaceId: string | undefined; value: Action | string }

/** 键匹配才取值：已切到别的 workspace 时一律视为无状态（渲染期过滤）。 */
function scopedValue<S extends Scoped>(
  state: S | null,
  workspaceId: string | undefined
): S['value'] | null {
  return state !== null && state.workspaceId === workspaceId
    ? state.value
    : null
}

export function usePreviewGovernance(workspaceId: string | undefined) {
  const publishMutation = usePublishPreviewPanel(workspaceId)
  const archiveMutation = useArchivePreviewPanel(workspaceId)
  const [pending, setPending] = useState<Scoped | null>(null)
  const [error, setError] = useState<Scoped | null>(null)

  function run(name: Action) {
    return async () => {
      const owner = workspaceId
      setError(null)
      setPending({ workspaceId: owner, value: name })
      try {
        await (
          name === 'publish' ? publishMutation : archiveMutation
        ).mutateAsync()
      } catch (cause) {
        setError({
          workspaceId: owner,
          value: cause instanceof Error ? cause.message : '操作失败',
        })
      } finally {
        // 只清自己那次的 pending：更晚发起的（同/异 workspace）不受影响。
        setPending((current) =>
          scopedValue(current, owner) === name ? null : current
        )
      }
    }
  }

  return {
    publishing: scopedValue(pending, workspaceId) === 'publish',
    actionError: scopedValue(error, workspaceId) as string | null,
    publish: () => void run('publish')(),
    archive: () => void run('archive')(),
  }
}
