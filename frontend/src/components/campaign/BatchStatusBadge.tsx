import { Chip } from '@mui/material'
import type { CampaignMode, CampaignStatus } from '../../types/campaignTypes'

/**
 * 批量任务的状态/类型文案（#532 PR-D 定稿）。
 *
 * 状态对齐全站标准词（labels.ts 的 STATUS_LABELS 口径）+ 批量任务语境的
 * 「投放中」（campaign 的 running 语义是按节奏投放，不是单任务执行）；
 * 类型只有三个：添加 / 重跑 / 升级。实现词（campaign / submit / rerun /
 * upgrade）不出现在文案里。
 */
const STATUS_CONFIG: Record<
  CampaignStatus,
  {
    label: string
    color: 'default' | 'primary' | 'warning' | 'success' | 'error'
  }
> = {
  pending: { label: '等待中', color: 'default' },
  running: { label: '投放中', color: 'primary' },
  paused: { label: '已暂停', color: 'warning' },
  failed: { label: '失败', color: 'error' },
  completed: { label: '已完成', color: 'success' },
  cancelled: { label: '已取消', color: 'default' },
}

const MODE_LABELS: Record<CampaignMode, string> = {
  submit: '添加',
  rerun: '重跑',
  upgrade: '升级',
}

export function batchModeLabel(mode: CampaignMode | string): string {
  return MODE_LABELS[mode as CampaignMode] ?? mode
}

export function batchStatusLabel(status: CampaignStatus): string {
  return STATUS_CONFIG[status].label
}

/** 批量任务状态徽章；类型文案导出给列表/详情共用。 */
export function BatchStatusBadge({ status }: { status: CampaignStatus }) {
  const config = STATUS_CONFIG[status]
  return (
    <Chip
      size="small"
      label={config.label}
      color={config.color}
      variant={status === 'pending' ? 'outlined' : 'filled'}
      data-testid={`batch-status-${status}`}
    />
  )
}

/** 类型 Chip：添加 / 重跑 / 升级。 */
export function BatchModeChip({ mode }: { mode: CampaignMode }) {
  return (
    <Chip
      size="small"
      variant="outlined"
      label={batchModeLabel(mode)}
      color={mode === 'submit' ? 'primary' : 'default'}
      data-testid={`batch-mode-${mode}`}
    />
  )
}
