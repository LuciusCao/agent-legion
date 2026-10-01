/**
 * 排查 Dock 的状态与接线（#795 PR③，从 JobDetailPage 拆出保体积预算）：
 * 头部「排查助手」给 job 级目标、失败节点「排查」入口给节点级目标——同一
 * 个 Dock 实例；jobId 切换即关闭（与页面其他弹层的 reset 同一模式）；
 * 挂载 key 按 workspace+job+node，换节点/workspace 重开时整棵重挂。
 */
import { useCallback, useRef, useState } from 'react'
import type { ReactElement } from 'react'
import { JobInspectDock } from '../../features/jobDiagnosis/JobInspectDock'
import type { JobDiagnosisTarget } from '../../features/jobDiagnosis/jobDiagnosisContext'

export function useJobInspectDock(
  workspaceId: string | undefined,
  jobId: string | undefined,
  jobTitle: string | undefined
): {
  inspectDock: ReactElement | null
  openInspect: () => void
  openNodeInspect: (node: { nodeKey: string; nodeLabel: string }) => void
} {
  const [inspectTarget, setInspectTarget] = useState<JobDiagnosisTarget | null>(
    null
  )
  // 焦点归还目标（#800 codex P2）：唤起瞬间的触发元素（点击/键盘触发时
  // 焦点已在按钮上）。key 重挂换节点时旧 Dock 实例的卸载清理会先把焦点
  // 还给旧触发元素——若新实例读挂载时的 activeElement 会错记成它；显式
  // ref 把归还目标绑定到最近一次唤起动作。
  const triggerRef = useRef<HTMLElement | null>(null)
  const recordTrigger = () => {
    triggerRef.current =
      document.activeElement instanceof HTMLElement
        ? document.activeElement
        : null
  }

  // jobId 切换即关闭：渲染期调整（state 记录前值的官方模式，与页面其他
  // 弹层的 reset 同一语义，不走 effect——避免 set-state-in-effect 的级联
  // 渲染）。
  const [prevJobId, setPrevJobId] = useState(jobId)
  if (prevJobId !== jobId) {
    setPrevJobId(jobId)
    setInspectTarget(null)
  }

  // 头部入口：job 级上下文（不带节点）。
  const openInspect = useCallback(() => {
    if (!workspaceId || !jobId) return
    recordTrigger()
    setInspectTarget({ workspaceId, jobId, jobTitle })
  }, [workspaceId, jobId, jobTitle])

  // 节点入口：节点上下文注入（与旧诊断弹窗的 target 语义等价）。
  const openNodeInspect = useCallback(
    (node: { nodeKey: string; nodeLabel: string }) => {
      if (!workspaceId || !jobId) return
      recordTrigger()
      setInspectTarget({
        workspaceId,
        jobId,
        jobTitle,
        nodeKey: node.nodeKey,
        nodeLabel: node.nodeLabel,
      })
    },

    [workspaceId, jobId, jobTitle]
  )

  const inspectDock = inspectTarget ? (
    <JobInspectDock
      key={`${inspectTarget.workspaceId}:${inspectTarget.jobId}:${inspectTarget.nodeKey ?? ''}`}
      target={inspectTarget}
      restoreFocusRef={triggerRef}
      onClose={() => setInspectTarget(null)}
    />
  ) : null

  return { inspectDock, openInspect, openNodeInspect }
}
