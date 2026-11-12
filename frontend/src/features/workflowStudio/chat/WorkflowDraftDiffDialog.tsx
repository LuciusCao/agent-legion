import { useState } from 'react'
import {
  Button,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
} from '@mui/material'
import { compareWorkflowDraft } from '../../../api/workflowDraftCompare'
import { buildChangeSummary } from '../validation/workflowStudioChanges'
import type { CompareResponse } from '../shared/useWorkflowDraftCompare.types'
import { WorkflowChangeSummaryPanel } from '../validation/WorkflowChangeSummaryPanel'
import { useStudioStateOptional } from '../shared/studioStateContext'
import styles from './StudioChatPanel.module.css'

/** 草稿卡「查看 diff」弹窗（M65 拆卡时从 WorkflowDraftCard 抽出，经典形态
 * 与结构卡共用）：草稿与 active revision 的 compare 摘要。compare 在
 * openDialog 点击处理器里触发（事件上下文），避免 effect 里 setState。 */
export function useWorkflowDraftDiffDialog(args: {
  workspaceId: string
  draftYaml: string
}) {
  const [open, setOpen] = useState(false)
  const [compare, setCompare] = useState<CompareResponse | null>(null)
  const [compareError, setCompareError] = useState<string | null>(null)

  function openDialog() {
    setOpen(true)
    setCompare(null)
    setCompareError(null)
    compareWorkflowDraft(args.workspaceId, {
      definition_yaml: args.draftYaml,
      // agent 起草场景允许空基线预览（从未发布的 workflow 展示全貌）。
      allow_missing_baseline: true,
    })
      .then(setCompare)
      .catch((error: unknown) => {
        setCompareError(error instanceof Error ? error.message : '对比失败')
      })
  }

  const dialog = (
    <WorkflowDraftDiffDialog
      open={open}
      compare={compare}
      compareError={compareError}
      onClose={() => setOpen(false)}
    />
  )
  return { dialog, openDialog }
}

function WorkflowDraftDiffDialog(props: {
  open: boolean
  compare: CompareResponse | null
  compareError: string | null
  onClose: () => void
}) {
  const studio = useStudioStateOptional()
  const summary = props.compare ? buildChangeSummary(props.compare) : null
  // diff 里的变更节点只有已应用进当前编辑器画布时才可选中定位：草稿未
  // 「应用到编辑器」时（尤其新增节点）不在 studio.nodes 中，选中会被
  // useStudioNodeSelection 立即清掉，变成无提示空操作——这些节点不可点，
  // 并在 dialog 里给一句提示。
  const canvasNodeKeys = new Set((studio?.nodes ?? []).map((node) => node.key))
  const hasUnlocatableNodes = Boolean(
    studio &&
    summary?.nodeChanges.some((change) => !canvasNodeKeys.has(change.nodeKey))
  )

  return (
    <Dialog open={props.open} onClose={props.onClose} maxWidth="md" fullWidth>
      <DialogTitle>草稿与 active revision 的差异</DialogTitle>
      <DialogContent dividers>
        {props.compareError && <p>{props.compareError}</p>}
        {!props.compareError && (
          <>
            <WorkflowChangeSummaryPanel
              summary={summary}
              loading={props.compare === null}
              errors={props.compare?.errors ?? null}
              onSelectNode={
                studio
                  ? (nodeKey) => {
                      if (!canvasNodeKeys.has(nodeKey)) return
                      // requestNodeFocus = bump 定位 nonce + 选中：目标已
                      // 是 selectedNodeKey 时（移动端在 Agent 面板点同一
                      // 节点）也能触发面板切换与镜头定位。
                      studio.requestNodeFocus(nodeKey)
                      props.onClose()
                    }
                  : undefined
              }
              isNodeSelectable={(nodeKey) => canvasNodeKeys.has(nodeKey)}
            />
            {hasUnlocatableNodes && (
              <p className={styles.draftHint} role="note">
                部分变更节点不在当前编辑器画布中，应用草稿后可定位
              </p>
            )}
          </>
        )}
      </DialogContent>
      <DialogActions>
        <Button onClick={props.onClose}>关闭</Button>
      </DialogActions>
    </Dialog>
  )
}
