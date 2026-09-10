import { Fragment, useState } from 'react'
import {
  Alert,
  Box,
  Button,
  CircularProgress,
  Collapse,
  LinearProgress,
  Paper,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
  Typography,
} from '@mui/material'
import { useQueryClient } from '@tanstack/react-query'
import { useUiStore } from '../../stores/uiStore'
import { queryKeys } from '../../lib/queryKeys'
import { formatDateTime } from '../../lib/formatters'
import {
  cancelCampaign,
  pauseCampaign,
  resumeCampaign,
} from '../../api/campaignApi'
import type { CampaignRecord } from '../../types/campaignTypes'
import { BatchModeChip, BatchStatusBadge } from './BatchStatusBadge'
import { BatchDetail } from './BatchDetail'
import { BatchCreateWizard } from './BatchCreateWizard'
import { batchDisplayName, batchSubLabel } from './batchListShared'
import { cursorProgress } from './campaignProgress'

type BatchListProps = {
  workspaceId: string
  campaigns: CampaignRecord[]
  loading: boolean
  error: string | null
}

/**
 * 「批量任务」列表页主体（#532 PR-D 定稿 IA）：任务（人话名+副行说明）/
 * 类型 / 状态 / 进度（文本+进度条）/ 创建时间 / 完成时间 / 操作（暂停⇄
 * 恢复、取消，终态无操作）。行点击展开详情（BatchDetail）；「新建批量
 * 任务」进三步向导。
 */
export function BatchList({
  workspaceId,
  campaigns,
  loading,
  error,
}: BatchListProps) {
  const queryClient = useQueryClient()
  const { showToast } = useUiStore()
  const [expandedId, setExpandedId] = useState<string | null>(null)
  const [wizardOpen, setWizardOpen] = useState(false)
  const [actionPending, setActionPending] = useState(false)

  const refresh = () => {
    void queryClient.invalidateQueries({
      queryKey: queryKeys.campaigns(workspaceId),
    })
  }

  const runAction = async (
    campaign: CampaignRecord,
    action: 'pause' | 'resume' | 'cancel'
  ) => {
    setActionPending(true)
    try {
      if (action === 'pause') await pauseCampaign(workspaceId, campaign.id)
      else if (action === 'resume')
        await resumeCampaign(workspaceId, campaign.id)
      else await cancelCampaign(workspaceId, campaign.id)
      showToast(
        action === 'pause'
          ? '已暂停'
          : action === 'resume'
            ? '已恢复'
            : '已取消',
        'success'
      )
      refresh()
    } catch (err) {
      showToast(err instanceof Error ? err.message : '操作失败', 'error')
    } finally {
      setActionPending(false)
    }
  }

  return (
    <Box sx={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
      <Box
        sx={{
          display: 'flex',
          gap: 1,
          alignItems: 'center',
          flexWrap: 'wrap',
        }}
      >
        <Typography variant="h6" component="h2">
          批量任务
        </Typography>
        <Box sx={{ flex: 1 }} />
        <Button
          variant="contained"
          size="small"
          onClick={() => setWizardOpen(true)}
          data-testid="batch-create-button"
        >
          新建批量任务
        </Button>
      </Box>
      <Typography variant="body2" color="text.secondary">
        大批量添加与重跑按队列水位自动分批执行，避免一次性打满执行队列
      </Typography>

      {error && <Alert severity="error">批量任务列表加载失败：{error}</Alert>}
      {loading && campaigns.length === 0 && (
        <Box sx={{ display: 'flex', justifyContent: 'center', py: 4 }}>
          <CircularProgress />
        </Box>
      )}
      {!loading && !error && campaigns.length === 0 && (
        <Alert severity="info">
          尚无批量任务。可在任务列表「多选 → 全选」后发起重跑/升级，或用
          右上角「新建批量任务」创建。
        </Alert>
      )}

      {campaigns.length > 0 && (
        <TableContainer component={Paper} variant="outlined">
          <Table size="small" data-testid="batch-list">
            <TableHead>
              <TableRow>
                <TableCell>任务</TableCell>
                <TableCell>类型</TableCell>
                <TableCell>状态</TableCell>
                <TableCell>进度</TableCell>
                <TableCell>创建时间</TableCell>
                <TableCell>完成时间</TableCell>
                <TableCell align="right">操作</TableCell>
              </TableRow>
            </TableHead>
            <TableBody>
              {campaigns.map((campaign) => {
                const expanded = expandedId === campaign.id
                const terminal =
                  campaign.status === 'failed' ||
                  campaign.status === 'completed' ||
                  campaign.status === 'cancelled'
                const { processed, total } = cursorProgress(campaign)
                const percent =
                  processed != null && total != null && total > 0
                    ? Math.min(100, Math.round((processed / total) * 100))
                    : null
                return (
                  <Fragment key={campaign.id}>
                    <TableRow
                      hover
                      onClick={() =>
                        setExpandedId(expanded ? null : campaign.id)
                      }
                      sx={{ cursor: 'pointer' }}
                    >
                      <TableCell>
                        <Typography variant="body2">
                          {batchDisplayName(campaign)}
                        </Typography>
                        <Typography variant="caption" color="text.secondary">
                          {batchSubLabel(campaign)}
                        </Typography>
                      </TableCell>
                      <TableCell>
                        <BatchModeChip mode={campaign.mode} />
                      </TableCell>
                      <TableCell>
                        <BatchStatusBadge status={campaign.status} />
                      </TableCell>
                      <TableCell sx={{ minWidth: 160 }}>
                        <Typography variant="caption">
                          {progressText(campaign)}
                        </Typography>
                        {percent != null ? (
                          <LinearProgress
                            variant="determinate"
                            value={percent}
                            sx={{ mt: 0.5 }}
                            data-testid="batch-progress-bar"
                          />
                        ) : (
                          <LinearProgress
                            variant="indeterminate"
                            sx={{ mt: 0.5, visibility: 'hidden' }}
                          />
                        )}
                      </TableCell>
                      <TableCell>
                        <Typography
                          variant="caption"
                          data-testid="batch-created-at"
                        >
                          {formatDateTime(campaign.created_at)}
                        </Typography>
                      </TableCell>
                      <TableCell>
                        <Typography
                          variant="caption"
                          data-testid="batch-finished-at"
                        >
                          {campaign.finished_at
                            ? formatDateTime(campaign.finished_at)
                            : '—'}
                        </Typography>
                      </TableCell>
                      <TableCell onClick={(event) => event.stopPropagation()}>
                        <Box
                          sx={{
                            display: 'flex',
                            gap: 0.5,
                            justifyContent: 'flex-end',
                          }}
                        >
                          {!terminal && campaign.status !== 'paused' && (
                            <Button
                              size="small"
                              disabled={actionPending}
                              onClick={() => void runAction(campaign, 'pause')}
                            >
                              暂停
                            </Button>
                          )}
                          {campaign.status === 'paused' && (
                            <Button
                              size="small"
                              disabled={actionPending}
                              onClick={() => void runAction(campaign, 'resume')}
                            >
                              恢复
                            </Button>
                          )}
                          {!terminal && (
                            <Button
                              size="small"
                              color="error"
                              disabled={actionPending}
                              onClick={() => void runAction(campaign, 'cancel')}
                            >
                              取消
                            </Button>
                          )}
                        </Box>
                      </TableCell>
                    </TableRow>
                    <TableRow key={`${campaign.id}-detail`}>
                      <TableCell
                        colSpan={7}
                        sx={{
                          py: 0,
                          borderBottom: expanded ? undefined : 'none',
                        }}
                      >
                        <Collapse in={expanded} unmountOnExit>
                          <BatchDetail
                            workspaceId={workspaceId}
                            campaignId={campaign.id}
                          />
                        </Collapse>
                      </TableCell>
                    </TableRow>
                  </Fragment>
                )
              })}
            </TableBody>
          </Table>
        </TableContainer>
      )}

      {wizardOpen && (
        <BatchCreateWizard
          open
          workspaceId={workspaceId}
          onClose={() => setWizardOpen(false)}
          onCreated={() => {
            setWizardOpen(false)
            refresh()
          }}
        />
      )}
    </Box>
  )
}

/** 进度列文本：三形态窄化后的可读读数。 */
function progressText(campaign: CampaignRecord): string {
  const { processed, total } = cursorProgress(campaign)
  if (processed == null) {
    return campaign.status === 'pending' ? '等待投放' : '—'
  }
  if (total != null && total > 0) {
    const percent = Math.min(100, Math.round((processed / total) * 100))
    return `${processed.toLocaleString()} / ${total.toLocaleString()}（${percent}%）`
  }
  return `已处理 ${processed.toLocaleString()} 个目标`
}
