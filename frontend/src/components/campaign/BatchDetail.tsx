import { Alert, Box, LinearProgress, Typography } from '@mui/material'
import { formatDateTime } from '../../lib/formatters'
import { useCampaign } from '../../hooks/useCampaign'
import { batchCountsLabel, batchDisplayName } from './batchListShared'
import { BatchModeChip, BatchStatusBadge } from './BatchStatusBadge'
import {
  campaignRuns,
  consecutiveFailures,
  cursorProgress,
  watermarkSamples,
} from './campaignProgress'

type BatchDetailProps = {
  workspaceId: string
  campaignId: string
}

/** 水位轨迹 sparkline：progress_json.watermark_samples 的纯 SVG 折线。 */
function WatermarkSparkline({
  samples,
  watermark,
}: {
  samples: { level: number; ts: number }[]
  watermark: number
}) {
  if (samples.length < 2) return null
  const width = 260
  const height = 64
  const levels = samples.map((s) => s.level)
  const maxLevel = Math.max(...levels, watermark, 1)
  const stepX = width / (samples.length - 1)
  const toY = (level: number) => height - (level / maxLevel) * (height - 4) - 2
  const points = samples
    .map((sample, index) => `${index * stepX},${toY(sample.level)}`)
    .join(' ')
  const watermarkY = toY(watermark)
  return (
    <svg
      width={width}
      height={height}
      role="img"
      aria-label="队列水位轨迹"
      data-testid="batch-watermark-sparkline"
    >
      <polyline
        points={points}
        fill="none"
        stroke="currentColor"
        strokeWidth="1.5"
      />
      <line
        x1="0"
        y1={watermarkY}
        x2={width}
        y2={watermarkY}
        stroke="currentColor"
        strokeDasharray="4 3"
        strokeOpacity="0.5"
      />
    </svg>
  )
}

/**
 * 批量任务详情面板（列表内展开形态）：执行节奏 / 目标进度 / 水位轨迹
 * sparkline / 失败原因与修复指引 / 添加类任务的关联运行概览 / 连续失败
 * 告警。数据来自 useCampaign 轮询（campaign 行即全部进度）。
 */
export function BatchDetail({ workspaceId, campaignId }: BatchDetailProps) {
  const { data, error } = useCampaign(workspaceId, campaignId)
  if (error) {
    return (
      <Alert severity="error" sx={{ my: 2 }}>
        批量任务详情加载失败：
        {error instanceof Error ? error.message : '未知错误'}
      </Alert>
    )
  }
  if (!data) return null
  const campaign = data.campaign
  const { processed, total } = cursorProgress(campaign)
  const samples = watermarkSamples(campaign)
  const failures = consecutiveFailures(campaign)
  const runs = campaignRuns(campaign)
  const percent =
    processed != null && total != null && total > 0
      ? Math.min(100, Math.round((processed / total) * 100))
      : null
  const terminal =
    campaign.status === 'failed' ||
    campaign.status === 'completed' ||
    campaign.status === 'cancelled'

  return (
    <Box
      sx={{
        display: 'flex',
        flexDirection: 'column',
        gap: 2,
        py: 2,
        maxWidth: 720,
      }}
      data-testid="batch-detail"
    >
      <Box
        sx={{ display: 'flex', gap: 1, alignItems: 'center', flexWrap: 'wrap' }}
      >
        <BatchStatusBadge status={campaign.status} />
        <BatchModeChip mode={campaign.mode} />
        <Typography variant="body2" color="text.secondary">
          ID {campaign.id}
        </Typography>
      </Box>

      <div>
        <Typography variant="body2" color="text.secondary">
          执行节奏
        </Typography>
        <Typography variant="body1">
          队列水位线 {campaign.watermark.toLocaleString()} · 每批{' '}
          {campaign.batch_size.toLocaleString()} 条
        </Typography>
        <Typography variant="body2" color="text.secondary">
          {campaign.batches_submitted > 0
            ? `已投放 ${campaign.batches_submitted} 批；服务端按水位自动分批，无需人工干预`
            : '尚未投放第一批；服务端按水位自动分批，无需人工干预'}
        </Typography>
      </div>

      <div>
        <Typography variant="body2" color="text.secondary">
          目标进度 · {batchCountsLabel(campaign)}
        </Typography>
        {percent != null && (
          <>
            <Typography variant="body2">
              {processed?.toLocaleString()} / {total?.toLocaleString()}（
              {percent}%）
            </Typography>
            <LinearProgress
              variant="determinate"
              value={percent}
              data-testid="batch-cursor-progress"
            />
          </>
        )}
        {processed != null && total == null && (
          <Typography variant="body2">
            已处理 {processed.toLocaleString()} 个目标（按筛选条件由服务端
            解析，总量随投放推进）
          </Typography>
        )}
      </div>

      {samples.length >= 2 && (
        <div>
          <Typography variant="body2" color="text.secondary">
            水位轨迹（最近 {samples.length} 次采样，虚线为水位线{' '}
            {campaign.watermark.toLocaleString()}）
          </Typography>
          <WatermarkSparkline
            samples={samples}
            watermark={campaign.watermark}
          />
        </div>
      )}

      {failures > 0 && (
        <Alert severity="warning">
          连续投放失败 {failures} 次（瞬态错误自动退避重试中，无需干预）
        </Alert>
      )}

      {campaign.error_message && (
        <Alert severity="error">
          失败原因：{campaign.error_message}
          <br />
          {terminal
            ? '修复问题后可重新创建同类批量任务；已处理的目标会自动跳过，不会重复执行。'
            : ''}
        </Alert>
      )}

      {campaign.mode === 'submit' && runs.length > 0 && (
        <div>
          <Typography variant="body2" color="text.secondary">
            关联运行（{runs.length}）
          </Typography>
          {runs.map((run) => (
            <Typography key={run.id} variant="body2">
              {run.id.slice(0, 8)} · 新建 {run.created_count} · 存量{' '}
              {run.job_count}
            </Typography>
          ))}
        </div>
      )}

      <Typography variant="caption" color="text.secondary">
        {batchDisplayName(campaign)} · 创建于{' '}
        {formatDateTime(campaign.created_at)}
        {campaign.finished_at
          ? ` · 完成于 ${formatDateTime(campaign.finished_at)}`
          : ''}
      </Typography>
    </Box>
  )
}
