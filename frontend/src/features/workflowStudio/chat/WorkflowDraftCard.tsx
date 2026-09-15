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
import {
  WorkflowDraftPublishButton,
  WorkflowDraftStaleHint,
} from './WorkflowDraftPublishAction'
import type { WorkflowDraftView } from './studioChatMessages'
import styles from './StudioChatPanel.module.css'

type WorkflowProps = {
  draft: WorkflowDraftView
  workspaceId: string
  onApply: (yaml: string) => void
}

export function WorkflowDraftCard(props: WorkflowProps) {
  const [diffOpen, setDiffOpen] = useState(false)
  const [compare, setCompare] = useState<CompareResponse | null>(null)
  const [compareError, setCompareError] = useState<string | null>(null)
  const studio = useStudioStateOptional()

  async function openDiff() {
    setDiffOpen(true)
    setCompare(null)
    setCompareError(null)
    try {
      setCompare(
        await compareWorkflowDraft(props.workspaceId, {
          definition_yaml: props.draft.yaml,
          // agent 起草场景允许空基线预览（从未发布的 workflow 展示全貌）。
          allow_missing_baseline: true,
        })
      )
    } catch (error) {
      setCompareError(error instanceof Error ? error.message : '对比失败')
    }
  }

  const summary = compare ? buildChangeSummary(compare) : null
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
    <div className={styles.draftCard}>
      <div className={styles.draftTitle}>📄 Workflow 草稿</div>
      <div className={styles.draftMeta}>
        {props.draft.compareMeta ?? 'agent 产出的定义草稿'}
        {props.draft.validated ? ' · 校验通过' : ' · 未通过校验'}
      </div>
      <div className={styles.draftActions}>
        <button type="button" className={styles.draftButton} onClick={openDiff}>
          查看 diff
        </button>
        <button
          type="button"
          className={`${styles.draftButton} ${styles.draftPrimary}`}
          onClick={() => props.onApply(props.draft.yaml)}
        >
          应用到编辑器
        </button>
        <WorkflowDraftPublishButton />
      </div>
      <WorkflowDraftStaleHint draftYaml={props.draft.yaml} />
      <Dialog
        open={diffOpen}
        onClose={() => setDiffOpen(false)}
        maxWidth="md"
        fullWidth
      >
        <DialogTitle>草稿与 active revision 的差异</DialogTitle>
        <DialogContent dividers>
          {compareError && <p>{compareError}</p>}
          {!compareError && (
            <>
              <WorkflowChangeSummaryPanel
                summary={summary}
                loading={compare === null}
                errors={compare?.errors ?? null}
                onSelectNode={
                  studio
                    ? (nodeKey) => {
                        if (!canvasNodeKeys.has(nodeKey)) return
                        // requestNodeFocus = bump 定位 nonce + 选中：目标已
                        // 是 selectedNodeKey 时（移动端在 Agent 面板点同一
                        // 节点）也能触发面板切换与镜头定位。
                        studio.requestNodeFocus(nodeKey)
                        setDiffOpen(false)
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
          <Button onClick={() => setDiffOpen(false)}>关闭</Button>
        </DialogActions>
      </Dialog>
    </div>
  )
}
