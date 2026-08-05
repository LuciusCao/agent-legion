import { Button, Chip } from '@mui/material'
import type { VideoArtifacts } from '../types'
import { useVideoNodeStore } from '../stores/videoNodeStore'
import { INTERACTION_TYPE_LABELS } from '../labels'
import { parseTimeSeconds } from '../lib/formatters'
import { RichText } from './RichText'
import {
  formatIssue,
  getGlobalStatus,
  getReviewMap,
  STATUS_LABELS,
} from './nodePanelHelpers'
import styles from './NodePanel.module.css'

interface NodePanelProps {
  onSeek?: (time: number) => void
  replayInteraction?: (index: number) => void
  artifacts?: VideoArtifacts
  triggeredNodeIndexes?: Set<number>
}

export function NodePanel({
  onSeek,
  replayInteraction,
  artifacts: artifactsProp,
  triggeredNodeIndexes: triggeredNodeIndexesProp,
}: NodePanelProps) {
  const { artifacts: storeArtifacts } = useVideoNodeStore()
  const { triggeredNodeIndexes: storeTriggeredNodeIndexes } =
    useVideoNodeStore()
  const artifacts = artifactsProp ?? storeArtifacts
  const triggeredNodeIndexes =
    triggeredNodeIndexesProp ?? storeTriggeredNodeIndexes
  const nodes = artifacts.interactions
  const reviewMap = getReviewMap(artifacts.review)
  const globalStatus = getGlobalStatus(artifacts.review)

  return (
    <div className="tab-panel">
      {nodes.map((node, i) => {
        const answered = triggeredNodeIndexes.has(i)
        const triggerTime = parseTimeSeconds(node.trigger_time ?? 0)
        const typeLabel =
          INTERACTION_TYPE_LABELS[String(node.type ?? '')] ||
          String(node.type ?? '')
        const nodeId = String(node.id ?? '')
        const nodeReview = nodeId ? reviewMap.get(nodeId) : undefined
        const status = nodeReview?.status || globalStatus
        const statusInfo = status ? STATUS_LABELS[status] : undefined
        const issues =
          nodeReview?.issues?.filter((issue) => formatIssue(issue)) ?? []

        return (
          <div
            key={node.id ?? i}
            className={`${styles.nodeCard} card-outlined ${answered ? styles.answered : ''}`}
            onClick={() => {
              onSeek?.(triggerTime)
              replayInteraction?.(i)
            }}
            style={{ cursor: 'pointer' }}
          >
            <div className={styles.nodeMain}>
              <span
                style={{
                  fontVariantNumeric: 'tabular-nums',
                  color: '#1a73e8',
                }}
              >
                {formatTime(triggerTime)}
              </span>
              <span>
                <RichText mode="inline">
                  {node.instruction || '交互节点'}
                </RichText>
              </span>
              <Chip label={typeLabel} size="small" />
            </div>
            {node.options && node.options.length > 0 && (
              <div
                style={{
                  display: 'flex',
                  gap: '8px',
                  flexWrap: 'wrap',
                  marginTop: '8px',
                }}
                onClick={(e) => e.stopPropagation()}
              >
                {node.options.map((opt, j) => (
                  <Button
                    key={opt.id ?? j}
                    variant="outlined"
                    disabled={answered}
                    size="small"
                  >
                    <RichText mode="inline">{opt.text}</RichText>
                  </Button>
                ))}
              </div>
            )}
            {statusInfo && (
              <div
                style={{
                  marginTop: '10px',
                  display: 'flex',
                  flexDirection: 'column',
                  gap: '4px',
                }}
              >
                <span
                  style={{
                    fontSize: '0.75rem',
                    fontWeight: 600,
                    color: statusInfo.color,
                  }}
                >
                  {statusInfo.text}
                </span>
                {issues.length > 0 && (
                  <ul
                    style={{
                      margin: 0,
                      paddingLeft: '16px',
                      fontSize: '0.75rem',
                      color: '#5f6368',
                    }}
                  >
                    {issues.map((issue, idx) => (
                      <li key={idx}>{formatIssue(issue)}</li>
                    ))}
                  </ul>
                )}
              </div>
            )}
          </div>
        )
      })}
    </div>
  )
}

function formatTime(seconds: number) {
  const m = Math.floor(seconds / 60)
  const s = Math.floor(seconds % 60)
  return `${m}:${s.toString().padStart(2, '0')}`
}
