import { useState } from 'react'
import {
  Button,
  Chip,
  CircularProgress,
  MenuItem,
  TextField,
} from '@mui/material'
import { toErrorMessage } from '../../lib/queryError'
import {
  useCreateReplay,
  useQualityReplayDetail,
  useQualityReplayProfiles,
  useQualityReplays,
} from '../../hooks/useQuality'
import type {
  QualityArtifactContent,
  QualityReplay,
} from '../../api/qualityApi'
import {
  ORIGINAL_CHOICE,
  createBody,
  originalChoiceLabel,
  optionLabel,
  optionValue,
  originalLabel,
  replayLabel,
} from './qualityReplayProfiles'
import {
  QualityArtifactView,
  QualityLabelHistory,
  formatQualityDateTime,
} from './QualityArtifactView'
import { QualityLabelForm } from './QualityLabelForm'
import styles from './QualityPanel.module.css'

function ReplayStatusChip({ status }: { status: string }) {
  if (status === 'succeeded') {
    return <Chip label="succeeded" size="small" color="success" />
  }
  if (status === 'failed') {
    return <Chip label="failed" size="small" color="error" />
  }
  return (
    <Chip
      icon={<CircularProgress size={12} />}
      label={status}
      size="small"
      variant="outlined"
    />
  )
}

interface ReplayDetailViewProps {
  workspaceId: string
  replayId: string
  originalArtifacts: QualityArtifactContent[]
  originalAgentVersion: number | null | undefined
}

/** 选中 replay 的详情：状态、新旧产物对比、replay 打标与标签历史。 */
function ReplayDetailView({
  workspaceId,
  replayId,
  originalArtifacts,
  originalAgentVersion,
}: ReplayDetailViewProps) {
  const query = useQualityReplayDetail(workspaceId, replayId)

  if (query.isLoading) return <p className={styles.muted}>加载中…</p>
  if (query.error) {
    return (
      <p className={styles.error}>
        Replay 详情加载失败：{toErrorMessage(query.error)}
      </p>
    )
  }

  const detail = query.data
  if (!detail) return null
  const { replay, artifacts, labels } = detail

  if (replay.status !== 'succeeded') {
    return (
      <div className={styles.replayDetail}>
        <p className={styles.muted}>
          {replay.status === 'failed'
            ? `Replay 失败：${replay.error_message || '未知错误'}`
            : 'Replay 进行中，完成后可查看对比…'}
        </p>
      </div>
    )
  }

  // 按产物名配对：原产物顺序优先，replay 独有的追加在后。
  const originalByName = new Map(originalArtifacts.map((a) => [a.name, a]))
  const replayByName = new Map(artifacts.map((a) => [a.name, a]))
  const names = [
    ...originalArtifacts.map((a) => a.name),
    ...artifacts.map((a) => a.name).filter((name) => !originalByName.has(name)),
  ]

  return (
    <div className={styles.replayDetail}>
      <div className={styles.compareGrid} aria-label="新旧产物对比">
        <div className={styles.compareHeader}>
          原产物（{originalLabel(originalAgentVersion)}）
        </div>
        <div className={styles.compareHeader}>
          Replay 产物（{replayLabel(replay)}）
        </div>
        {names.map((name) => {
          const before = originalByName.get(name)
          const after = replayByName.get(name)
          return (
            <div key={name} className={styles.compareRow}>
              <div>
                <div className={styles.compareName}>{name}</div>
                {before ? (
                  <QualityArtifactView artifact={before} />
                ) : (
                  <p className={styles.muted}>原运行无此产物</p>
                )}
              </div>
              <div>
                <div className={styles.compareName}>{name}</div>
                {after ? (
                  <QualityArtifactView artifact={after} />
                ) : (
                  <p className={styles.muted}>Replay 无此产物</p>
                )}
              </div>
            </div>
          )
        })}
        {names.length === 0 && (
          <p className={styles.muted}>原运行与 Replay 均无产物</p>
        )}
      </div>

      <section aria-label="Replay 打标">
        <h4>Replay 打标</h4>
        <QualityLabelForm
          workspaceId={workspaceId}
          itemId={replay.item_id}
          replayId={replay.id}
          verdictLabel="Replay 结论"
          reasonLabel="Replay 原因码"
          submitText="提交 Replay 打标"
        />
      </section>
      <section aria-label="Replay 标签历史">
        <h4>Replay 标签历史</h4>
        <QualityLabelHistory labels={labels} />
      </section>
    </div>
  )
}

export interface QualityReplaySectionProps {
  workspaceId: string
  itemId: string
  originalArtifacts: QualityArtifactContent[]
  originalAgentVersion: number | null | undefined
}

/** Replay 区：发起 replay（#1079 / #440 D6：按 workflow revision 或当前草稿
 * 选执行档案，默认原运行的执行档案）、replay 列表、对比与打标。 */
export function QualityReplaySection({
  workspaceId,
  itemId,
  originalArtifacts,
  originalAgentVersion,
}: QualityReplaySectionProps) {
  const [choice, setChoice] = useState(ORIGINAL_CHOICE)
  const [selectedReplayId, setSelectedReplayId] = useState<string | null>(null)
  const [createError, setCreateError] = useState('')
  const replaysQuery = useQualityReplays(workspaceId, itemId)
  const profilesQuery = useQualityReplayProfiles(workspaceId, itemId)
  const mutation = useCreateReplay(workspaceId, itemId)

  const replays = replaysQuery.data?.replays ?? []
  const options = profilesQuery.data?.options ?? []

  const handleCreate = async () => {
    setCreateError('')
    try {
      const result = await mutation.mutateAsync(createBody(choice))
      setChoice(ORIGINAL_CHOICE)
      setSelectedReplayId(result.replay.id)
    } catch (err) {
      const status = (err as { status?: number }).status
      setCreateError(
        status === 409
          ? '该样本已有进行中的 replay，请等待完成后再发起'
          : toErrorMessage(err)
      )
    }
  }

  return (
    <div>
      <div className={styles.createRow}>
        <TextField
          select
          label="执行档案"
          value={choice}
          onChange={(e) => setChoice(e.target.value)}
          size="small"
          sx={{ minWidth: 280 }}
        >
          <MenuItem value={ORIGINAL_CHOICE}>
            {originalChoiceLabel(originalAgentVersion)}
          </MenuItem>
          {options.map((option) => (
            <MenuItem key={optionValue(option)} value={optionValue(option)}>
              {optionLabel(option)}
            </MenuItem>
          ))}
        </TextField>
        <Button
          variant="contained"
          size="small"
          onClick={handleCreate}
          disabled={mutation.isPending}
        >
          {mutation.isPending ? '发起中…' : '发起 Replay'}
        </Button>
      </div>
      {createError && <p role="alert">{createError}</p>}

      {replaysQuery.isLoading && <p className={styles.muted}>加载中…</p>}
      {!replaysQuery.isLoading && replays.length === 0 && (
        <p className={styles.muted}>暂无 replay</p>
      )}
      <ul className={styles.itemListUl}>
        {replays.map((replay: QualityReplay) => (
          <li key={replay.id}>
            <button
              type="button"
              className={
                replay.id === selectedReplayId
                  ? styles.itemRowActive
                  : styles.itemRow
              }
              onClick={() => setSelectedReplayId(replay.id)}
              aria-current={replay.id === selectedReplayId || undefined}
            >
              <span className={styles.itemRowHeader}>
                <strong>{replayLabel(replay)}</strong>
                <ReplayStatusChip status={replay.status} />
                <span className={styles.itemMeta}>
                  {formatQualityDateTime(replay.created_at)}
                </span>
              </span>
              {replay.status === 'failed' && replay.error_message && (
                <span className={styles.itemMeta}>{replay.error_message}</span>
              )}
            </button>
          </li>
        ))}
      </ul>

      {selectedReplayId && (
        <ReplayDetailView
          key={selectedReplayId}
          workspaceId={workspaceId}
          replayId={selectedReplayId}
          originalArtifacts={originalArtifacts}
          originalAgentVersion={originalAgentVersion}
        />
      )}
    </div>
  )
}
