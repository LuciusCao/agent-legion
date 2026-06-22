import { useEffect, useRef, useState } from 'react'
import {
  Button,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
} from '@mui/material'
import { fetchJobLog } from '../jobApi'
import type { JobLogResponse } from '../jobApi'
import { MaterialIcon } from './MaterialIcon'
import styles from './JobLogDialog.module.css'

export interface JobLogDialogProps {
  jobId: string
  runId: number
  nodeLabel: string
  open: boolean
  onClose: () => void
}

type LogEntry = NonNullable<JobLogResponse['structured']>[number]

const ENTRY_ICONS: Record<string, string> = {
  thinking: 'smart_toy',
  tool_call: 'build_circle',
  tool_result: 'check_circle',
  message: 'forum',
  error: 'error',
  stderr: 'stream',
  session: 'help',
  raw: 'description',
}

// Entry types that are collapsed by default so the user sees the chain first,
// then can drill into long tool results or internal thinking.
const DEFAULT_COLLAPSED_TYPES = new Set(['thinking', 'tool_result'])

function formatEntryType(type: string): string {
  const labels: Record<string, string> = {
    thinking: '思考',
    tool_call: '工具调用',
    tool_result: '工具结果',
    message: '回复',
    error: '错误',
    stderr: '标准错误',
    session: '会话',
    raw: '原始日志',
  }
  return labels[type] || type
}

export function JobLogDialog({
  jobId,
  runId,
  nodeLabel,
  open,
  onClose,
}: JobLogDialogProps) {
  const [log, setLog] = useState<JobLogResponse | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [expanded, setExpanded] = useState<Set<string>>(new Set())
  const requestIdRef = useRef(0)

  useEffect(() => {
    if (!open) {
      requestIdRef.current += 1
      // eslint-disable-next-line react-hooks/set-state-in-effect
      setLog(null)
      setError('')
      setLoading(false)
      setExpanded(new Set())
      return
    }

    const requestId = ++requestIdRef.current
    setLoading(true)
    setError('')
    setLog(null)
    setExpanded(new Set())

    fetchJobLog(jobId, runId)
      .then((data) => {
        if (requestId !== requestIdRef.current) return
        const initialExpanded = new Set<string>()
        data.structured?.forEach((entry, index) => {
          const key = `${entry.type}-${index}`
          if (!DEFAULT_COLLAPSED_TYPES.has(entry.type)) {
            initialExpanded.add(key)
          }
        })
        setExpanded(initialExpanded)
        setLog(data)
      })
      .catch((err) => {
        if (requestId !== requestIdRef.current) return
        setError(err instanceof Error ? err.message : String(err))
      })
      .finally(() => {
        if (requestId === requestIdRef.current) {
          setLoading(false)
        }
      })
  }, [open, jobId, runId])

  const toggleEntry = (key: string) => {
    setExpanded((prev) => {
      const next = new Set(prev)
      if (next.has(key)) {
        next.delete(key)
      } else {
        next.add(key)
      }
      return next
    })
  }

  const structured = log?.structured
  const hasStructured = !!structured && structured.length > 0

  return (
    <Dialog
      open={open}
      onClose={onClose}
      PaperProps={{ sx: { maxWidth: '960px', width: '95vw' } }}
    >
      <DialogTitle>
        <div className={styles.titleRow}>
          <span>日志 — {nodeLabel}</span>
          {log?.raw_url && (
            <Button
              size="small"
              variant="text"
              href={log.raw_url}
              target="_blank"
              startIcon={<MaterialIcon name="download" />}
            >
              原始日志
            </Button>
          )}
        </div>
      </DialogTitle>
      <DialogContent className={styles.content}>
        {loading && <p className={styles.empty}>加载中...</p>}
        {!loading && error && <p className={styles.error}>{error}</p>}
        {!loading && !error && log && log.log === '' && !hasStructured && (
          <p className={styles.empty}>暂无日志</p>
        )}
        {!loading && !error && log && hasStructured && (
          <div className={styles.entries}>
            {structured!.map((entry: LogEntry, index: number) => {
              const icon = ENTRY_ICONS[entry.type] || 'description'
              const detail = entry.detail || ''
              // The structured list is append-only and static while the dialog is
              // open, so a composite key based on type + position is sufficient.
              const expandKey = `${entry.type}-${index}`
              const isExpanded = expanded.has(expandKey)
              const needsExpand =
                DEFAULT_COLLAPSED_TYPES.has(entry.type) ||
                entry.truncated ||
                detail.length > 300
              return (
                <div key={expandKey} className={styles.entry}>
                  <div className={styles.entryHeader}>
                    <MaterialIcon name={icon} className={styles.entryIcon} />
                    <span className={styles.entryTitle}>
                      {entry.title || formatEntryType(entry.type)}
                    </span>
                    {needsExpand && (
                      <button
                        type="button"
                        className={styles.expandBtn}
                        onClick={() => toggleEntry(expandKey)}
                      >
                        {isExpanded ? '收起' : '展开'}
                      </button>
                    )}
                  </div>
                  <pre
                    className={[
                      styles.entryDetail,
                      isExpanded ? styles.entryDetailExpanded : '',
                    ].join(' ')}
                  >
                    {detail}
                  </pre>
                </div>
              )
            })}
            {log.truncated && (
              <p className={styles.hint}>仅显示尾部日志，完整内容已截断</p>
            )}
          </div>
        )}
        {!loading && !error && log && !hasStructured && log.log !== '' && (
          <>
            <pre className={styles.pre}>{log.log}</pre>
            {log.truncated && (
              <p className={styles.hint}>仅显示尾部日志，完整内容已截断</p>
            )}
          </>
        )}
      </DialogContent>
      <DialogActions>
        <Button variant="text" onClick={onClose}>
          关闭
        </Button>
      </DialogActions>
    </Dialog>
  )
}
