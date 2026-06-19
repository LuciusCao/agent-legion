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
import styles from './JobLogDialog.module.css'

export interface JobLogDialogProps {
  jobId: string
  runId: number
  nodeLabel: string
  open: boolean
  onClose: () => void
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
  const requestIdRef = useRef(0)

  useEffect(() => {
    if (!open) {
      requestIdRef.current += 1
      // eslint-disable-next-line react-hooks/set-state-in-effect
      setLog(null)
      setError('')
      setLoading(false)
      return
    }

    const requestId = ++requestIdRef.current
    setLoading(true)
    setError('')
    setLog(null)

    fetchJobLog(jobId, runId)
      .then((data) => {
        if (requestId !== requestIdRef.current) return
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

  return (
    <Dialog
      open={open}
      onClose={onClose}
      PaperProps={{ sx: { maxWidth: '760px', width: '90vw' } }}
    >
      <DialogTitle>日志 — {nodeLabel}</DialogTitle>
      <DialogContent className={styles.content}>
        {loading && <p className={styles.empty}>加载中...</p>}
        {!loading && error && <p className={styles.error}>{error}</p>}
        {!loading && !error && log && log.log === '' && (
          <p className={styles.empty}>暂无日志</p>
        )}
        {!loading && !error && log && log.log !== '' && (
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
