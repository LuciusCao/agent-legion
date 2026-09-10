import { useMemo, useState } from 'react'
import { Alert, Dialog, DialogContent, DialogTitle } from '@mui/material'
import { useQueryClient } from '@tanstack/react-query'
import { useUiStore } from '../../stores/uiStore'
import { useJobStore } from '../../stores/jobStore'
import { queryKeys } from '../../lib/queryKeys'
import { createCampaign } from '../../api/campaignApi'
import type { JobListFilterParams } from '../../types/jobTypes'
import type { JobSummary } from '../../types'
import type { NodeCatalog } from '../../lib/nodeCatalog'
import {
  computeOrderedNodes,
  type WorkflowNodesByKey,
} from '../../lib/workflowNodes'
import type { FailureCategorySelection } from '../JobRerunDialog/failureCategoryCounts'
import { JobAllMatchingNodeRow } from './JobAllMatchingNodeRow'
import { JobAllMatchingFailureCategoryRow } from './JobAllMatchingFailureCategoryRow'
import { JobAllMatchingRerunFooter } from './JobAllMatchingRerunFooter'
import { useBatchRerunPreview } from './useBatchRerunPreview'

export type JobAllMatchingRerunDialogProps = {
  open: boolean
  count: number
  jobs: JobSummary[]
  workspaceId?: string
  workflowDefinition?: NodeCatalog | null
  workflowNodesByKey?: WorkflowNodesByKey | null
  onClose: () => void
  /**
   * 失败类别（非 all）的确认仍走原同步 rerun-by-failure 端点：它是
   * 服务端按类别过滤的独立路径（不在 #532 的无护栏端点清单里，
   * 设计 §5.2），只有节点/全部失败（原 batch-rerun 全量形态）改走
   * 批量任务。
   */
  onConfirmCategory?: (
    category: Exclude<FailureCategorySelection, 'all'>
  ) => void | Promise<void>
}

/**
 * Rerun dialog for 'allMatching' selections（#532 PR-D 定稿）：
 * from-node / 全部失败的 filter 全量重跑创建**批量任务**（用户语言；
 * 实现上是 rerun campaign）——同步路径在全量形态下没有切片与水位护栏，
 * 正是批量任务产品化的动机（设计 §4.1/§5.2）。失败类别选择维持原同步
 * rerun-by-failure 路径（服务端按类别过滤，不在无护栏清单里）。
 *
 * 交互流：选择节点/失败类别 → 既有 preview 端点回答「将重跑 N 个任务」
 * （批量任务试算与之共享判定，数目不漂移）→ 确认创建批量任务（携带同一
 * selection filter 与反选）→ 服务端按执行节奏分批投放。任务名自动取
 * 「重跑 · 目标摘要」。
 */
export function JobAllMatchingRerunDialog({
  open,
  count,
  jobs,
  workspaceId,
  workflowDefinition,
  workflowNodesByKey,
  onClose,
  onConfirmCategory,
}: JobAllMatchingRerunDialogProps) {
  const queryClient = useQueryClient()
  const { showToast } = useUiStore()
  const [selection, setSelection] = useState<FailureCategorySelection>('all')
  const [selectedNodeKey, setSelectedNodeKey] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)
  // selection filter 与 preview/同步路径同源（useBatchRerunPreview 也是
  // 订阅这两个字段再 resolveBatchTarget）；批量任务的 target_spec 需要
  // 同一份 filter，订阅而非快照保证一致。
  const selectionFilter = useJobStore((s) => s.selectionFilter)
  // allMatching 的反选（P2-1）：旧同步路径 resolveBatchTarget 传
  // filter + excludeIds，批量任务创建必须带上同一份反选，否则被反选的
  // 任务会被翻回重跑（对话框计数已减去它们）。
  const excludedIds = useJobStore((s) => s.excludedIds)

  const orderedNodes = useMemo(
    () => computeOrderedNodes(jobs, workflowDefinition, workflowNodesByKey),
    [jobs, workflowDefinition, workflowNodesByKey]
  )

  const preview = useBatchRerunPreview(
    workspaceId,
    open,
    selectedNodeKey
      ? { kind: 'node', nodeKey: selectedNodeKey }
      : selection === 'all'
        ? { kind: 'failedNode' }
        : { kind: 'category', category: selection }
  )

  if (!open) return null

  const nodeLabel = selectedNodeKey
    ? (orderedNodes.find((node) => node.key === selectedNodeKey)?.label ??
      selectedNodeKey)
    : null

  const handleConfirm = async () => {
    // 特定失败类别：原同步 rerun-by-failure 路径。store 已弹错误 toast，
    // 这里再消费拒绝本身——footer 不 await 返回的 Promise，不 catch 会
    // 变成未处理 rejection（codex 二轮 P2）；失败时对话框保持打开。
    if (selectedNodeKey == null && selection !== 'all') {
      try {
        await onConfirmCategory?.(selection)
      } catch {
        return
      }
      onClose()
      return
    }
    if (!workspaceId) return
    setLoading(true)
    try {
      await createCampaign(
        workspaceId,
        'rerun',
        {
          // 节点模式选 node_key；全部失败模式走 from_failed_node。
          from_failed_node: selectedNodeKey == null,
          node_key: selectedNodeKey ?? null,
          filter: (selectionFilter ?? {}) as JobListFilterParams,
          job_ids: null,
          // 反选透传（P2-1）：服务端切片在 SQL 里排除这些 id。
          exclude_ids: Array.from(excludedIds),
        },
        // 自动命名：类型 + 目标摘要（定稿 §4；列表显示人话名）。
        nodeLabel ? `重跑 · 从「${nodeLabel}」节点` : '重跑 · 全部失败任务'
      )
      showToast('批量任务已创建，进度可在「批量任务」页查看', 'success')
      void queryClient.invalidateQueries({
        queryKey: queryKeys.campaigns(workspaceId),
      })
    } catch (err) {
      showToast(
        err instanceof Error ? err.message : '创建批量任务失败',
        'error'
      )
      return
    } finally {
      setLoading(false)
    }
    onClose()
  }

  return (
    <Dialog open onClose={onClose}>
      <DialogTitle>批量重跑</DialogTitle>
      <DialogContent>
        <p>将对符合筛选条件的 {count} 个任务执行（按筛选条件由服务端解析）</p>
        <Alert severity="info" sx={{ mb: 2 }} data-testid="batch-notice">
          全量重跑会创建批量任务分批执行：服务端按执行节奏自动投放，不再
          一次性翻回全部任务。可在「批量任务」页查看进度并暂停/恢复。
        </Alert>
        <JobAllMatchingNodeRow
          nodes={orderedNodes}
          selectedNodeKey={selectedNodeKey}
          onSelectNode={setSelectedNodeKey}
        />
        <JobAllMatchingFailureCategoryRow
          active={selectedNodeKey === null}
          selection={selection}
          onSelect={(value) => {
            setSelectedNodeKey(null)
            setSelection(value)
          }}
        />
      </DialogContent>
      <JobAllMatchingRerunFooter
        eligibleCount={preview.data?.eligible_count}
        selectedNodeLabel={nodeLabel}
        loading={loading}
        count={count}
        onClose={onClose}
        onConfirm={handleConfirm}
      />
    </Dialog>
  )
}
