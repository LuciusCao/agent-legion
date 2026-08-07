import { Chip } from '@mui/material'
import { JsonTree } from '../JsonTree'
import type { QualityArtifactContent, QualityLabel } from '../../api/qualityApi'
import styles from './QualityPanel.module.css'

// prettier-ignore
const tryParseJson = (content: string): unknown | null => { try { return JSON.parse(content) } catch { return null } }

export function formatQualityDateTime(iso: string): string {
  const date = new Date(iso)
  return Number.isNaN(date.getTime()) ? iso : date.toLocaleString('zh-CN')
}

/** 单个产物内容：.json 且可解析时用 JsonTree，否则原文 <pre>。 */
export function QualityArtifactView({
  artifact,
}: {
  artifact: QualityArtifactContent
}) {
  const parsedJson = artifact.name.endsWith('.json')
    ? tryParseJson(artifact.content)
    : null
  return (
    <div className={styles.artifact}>
      <div className={styles.artifactName}>
        {artifact.name}
        {artifact.truncated && (
          <Chip label="已截断" size="small" variant="outlined" sx={{ ml: 1 }} />
        )}
      </div>
      {parsedJson !== null ? (
        <JsonTree data={parsedJson} />
      ) : (
        <pre className={styles.pre}>{artifact.content}</pre>
      )}
    </div>
  )
}

export function QualityLabelHistory({ labels }: { labels: QualityLabel[] }) {
  if (labels.length === 0) {
    return <p className={styles.muted}>暂无打标记录</p>
  }
  return (
    <ul className={styles.history}>
      {labels.map((label) => (
        <li key={label.id} className={styles.historyRow}>
          <Chip
            label={label.verdict === 'good' ? 'good' : 'bad'}
            size="small"
            color={label.verdict === 'good' ? 'success' : 'error'}
          />
          <span>{label.labeled_by}</span>
          <span className={styles.muted}>
            {formatQualityDateTime(label.created_at)}
          </span>
          {(label.reason_codes ?? []).length > 0 && (
            <span className={styles.muted}>
              [{(label.reason_codes ?? []).join(', ')}]
            </span>
          )}
          {label.note && <span className={styles.muted}>{label.note}</span>}
        </li>
      ))}
    </ul>
  )
}
