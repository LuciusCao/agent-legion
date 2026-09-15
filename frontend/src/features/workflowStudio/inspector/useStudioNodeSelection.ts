import { useCallback, useEffect, useState } from 'react'
import type { DagGraphNode } from '../../../components/dag/DagGraph'
import type { UseWorkflowStudioDraftResult } from '../shared/useWorkflowStudioDraft'

/** 画布节点选择及其生命周期：切换 workspace 清空选择（不同 workflow 极易
 * 撞同名 key，如 _start/intake）；选中节点从画布消失（YAML 删除/改名、
 * ghost 预览刷新）时自动清除，避免空详情面板把 DAG 顶掉。
 * focusNonce 是独立于选中值变化的「定位/聚焦请求」信号：目标节点已是
 * selectedNodeKey 时 setter 写入相同值不触发更新，移动端面板切换与画布
 * 镜头定位都以 key 变化为触发点会漏响应（如移动端在 Agent 面板点草稿
 * diff 里已选中的同一节点）。每次定位请求 bump nonce，消费方按「key 变化
 * 或 nonce 变化」响应。 */
export function useStudioNodeSelection(
  workspaceId: string | undefined,
  nodes: DagGraphNode[]
) {
  const [selectedNodeKey, setSelectedNodeKey] = useState<string | null>(null)
  const [focusNonce, setFocusNonce] = useState(0)
  const requestNodeFocus = useCallback((nodeKey: string) => {
    setFocusNonce((nonce) => nonce + 1)
    setSelectedNodeKey(nodeKey)
  }, [])
  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- workspace 切换是有意的选择重置点
    setSelectedNodeKey(null)
  }, [workspaceId])
  useEffect(() => {
    if (
      selectedNodeKey &&
      !nodes.some((node) => node.key === selectedNodeKey)
    ) {
      // eslint-disable-next-line react-hooks/set-state-in-effect -- 选择跟随画布节点集合自愈
      setSelectedNodeKey(null)
    }
  }, [nodes, selectedNodeKey])
  return { selectedNodeKey, setSelectedNodeKey, focusNonce, requestNodeFocus }
}

type RevisionDraft = Pick<
  UseWorkflowStudioDraftResult,
  'selectRevision' | 'backToDraft' | 'useViewedRevisionAsDraft'
>

/** revision 切换动作的公共包装：切换/返回草稿/取为草稿后都清空节点选择。 */
export function buildStudioRevisionActions(
  draft: RevisionDraft,
  setSelectedNodeKey: (key: string | null) => void
) {
  return {
    selectRevision: async (revisionId: string) => {
      await draft.selectRevision(revisionId)
      setSelectedNodeKey(null)
    },
    backToDraft: () => {
      draft.backToDraft()
      setSelectedNodeKey(null)
    },
    useViewedRevisionAsDraft: () => {
      draft.useViewedRevisionAsDraft()
      setSelectedNodeKey(null)
    },
  }
}
