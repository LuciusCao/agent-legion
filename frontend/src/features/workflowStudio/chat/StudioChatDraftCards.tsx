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
import { useStudioNav } from '../shared/workflowStudioNav'
import type {
  AgentDefinitionDraftView,
  NodeCodeDraftView,
  WorkflowDraftView,
} from './studioChatMessages'
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
      </div>
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
            <WorkflowChangeSummaryPanel
              summary={compare ? buildChangeSummary(compare) : null}
              loading={compare === null}
              errors={compare?.errors ?? null}
            />
          )}
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setDiffOpen(false)}>关闭</Button>
        </DialogActions>
      </Dialog>
    </div>
  )
}

export function AgentDefinitionDraftCard({
  draft,
}: {
  draft: AgentDefinitionDraftView
}) {
  const nav = useStudioNav()
  const meta = [
    draft.skill ? `skill: ${draft.skill}` : null,
    draft.runtime ? `runtime: ${draft.runtime}` : null,
  ]
    .filter(Boolean)
    .join(' · ')
  return (
    <div className={styles.draftCard}>
      <div className={styles.draftTitle}>
        🤖 Agent 定义草稿：{draft.agentId}
      </div>
      {meta && <div className={styles.draftMeta}>{meta}</div>}
      <div className={styles.draftActions}>
        <button
          type="button"
          className={styles.draftButton}
          onClick={() => nav.openAgent(draft.agentId)}
        >
          查看草稿
        </button>
      </div>
    </div>
  )
}

export function NodeCodeDraftCard(props: {
  draft: NodeCodeDraftView
  onSelectNode?: (nodeKey: string) => void
}) {
  return (
    <div className={styles.draftCard}>
      <div className={styles.draftTitle}>
        🧩 节点代码草稿：{props.draft.nodeKey}
      </div>
      <div className={styles.draftMeta}>仅草稿，发布前不会在 job 中运行</div>
      {props.onSelectNode && (
        <div className={styles.draftActions}>
          <button
            type="button"
            className={styles.draftButton}
            onClick={() => props.onSelectNode!(props.draft.nodeKey)}
          >
            查看草稿
          </button>
        </div>
      )}
    </div>
  )
}
