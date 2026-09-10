import { useState } from 'react'
import {
  Alert,
  Button,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
} from '@mui/material'
import { useQueryClient } from '@tanstack/react-query'
import { useUiStore } from '../../stores/uiStore'
import { useJobStore } from '../../stores/jobStore'
import { queryKeys } from '../../lib/queryKeys'
import { createCampaign } from '../../api/campaignApi'
import type { JobListFilterParams } from '../../types/jobTypes'

interface JobAllMatchingUpgradeDialogProps {
  open: boolean
  count: number
  workspaceId?: string
  onClose: () => void
}

/**
 * Upgrade dialog for 'allMatching' selections（#532 PR-D 定稿）：filter
 * 全量的 workflow 升级创建**批量任务**（实现上是 upgrade campaign）——
 * 升级会把每个任务翻回非终态从头重跑（设计 §0 对 #532 前提冲突的
 * 核实），全量形态与批量重跑同样需要节奏闸控。显式 ids 小批量的
 * JobActionBarUpgrade（per-job 循环）维持不动。文案不出现实现词。
 */
export function JobAllMatchingUpgradeDialog({
  open,
  count,
  workspaceId,
  onClose,
}: JobAllMatchingUpgradeDialogProps) {
  const queryClient = useQueryClient()
  const { showToast } = useUiStore()
  const selectionFilter = useJobStore((s) => s.selectionFilter)
  // allMatching 的反选（P2-1）：与重跑对话框同一纪律——创建参数带上
  // store 的 excludedIds，切片排除在服务端。
  const excludedIds = useJobStore((s) => s.excludedIds)
  const [isUpgrading, setIsUpgrading] = useState(false)

  if (!open) return null

  const handleConfirm = async () => {
    if (!workspaceId) return
    setIsUpgrading(true)
    try {
      await createCampaign(
        workspaceId,
        'upgrade',
        {
          from_failed_node: false,
          node_key: null,
          filter: (selectionFilter ?? {}) as JobListFilterParams,
          job_ids: null,
          exclude_ids: Array.from(excludedIds),
        },
        // 自动命名（定稿 §4）：类型 + 目标摘要。
        '升级 · 旧版本存量任务'
      )
      showToast('批量任务已创建，进度可在「批量任务」页查看', 'success')
      void queryClient.invalidateQueries({
        queryKey: queryKeys.campaigns(workspaceId),
      })
      onClose()
    } catch (err) {
      showToast(
        err instanceof Error ? err.message : '创建批量任务失败',
        'error'
      )
    } finally {
      setIsUpgrading(false)
    }
  }

  return (
    <Dialog open={open} onClose={onClose}>
      <DialogTitle>批量升级</DialogTitle>
      <DialogContent>
        <p>
          将对符合筛选条件的 {count} 个任务执行 workflow 升级。已是最新版本
          或运行中的任务会自动跳过。
        </p>
        <Alert severity="info" data-testid="batch-notice">
          全量升级会创建批量任务分批执行：升级会把任务翻回队列从头重跑，
          服务端按执行节奏自动投放。可在「批量任务」页查看进度并暂停/恢复。
        </Alert>
      </DialogContent>
      <DialogActions>
        <Button variant="text" onClick={onClose} disabled={isUpgrading}>
          取消
        </Button>
        <Button
          variant="contained"
          onClick={handleConfirm}
          disabled={isUpgrading}
        >
          {isUpgrading ? '创建中...' : '创建批量任务'}
        </Button>
      </DialogActions>
    </Dialog>
  )
}
