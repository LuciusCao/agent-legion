import { useEffect, useState } from 'react'
import { fetchJobArtifact } from '../../api'
import styles from './NonUploadableNotice.module.css'

export interface NonUploadableNoticeProps {
  jobId: string
  jobStatus: string
  artifacts: string[]
}

interface ManifestShape {
  uploadable?: boolean
  skip_reason?: string
}

/**
 * Muted notice for jobs that completed via a non-uploadable terminal node
 * (e.g. a question with no text stem): the job is a clean terminal state,
 * not a failure, so the skip reason comes from the written manifest.
 */
export function NonUploadableNotice({
  jobId,
  jobStatus,
  artifacts,
}: NonUploadableNoticeProps) {
  // Keyed by jobId: a stale result from the previously viewed job never
  // renders, and is replaced once this job's manifest resolves.
  const [result, setResult] = useState<{
    jobId: string
    reason: string
  } | null>(null)
  const shouldCheck =
    jobStatus === 'completed' && artifacts.includes('manifest.json')

  useEffect(() => {
    if (!shouldCheck) return
    let cancelled = false
    fetchJobArtifact(jobId, 'manifest.json')
      .then((artifact) => {
        if (cancelled) return
        try {
          const manifest = JSON.parse(artifact.content) as ManifestShape
          if (manifest.uploadable === false) {
            setResult({ jobId, reason: manifest.skip_reason || '' })
          }
        } catch {
          // A non-JSON manifest carries no uploadable verdict; stay quiet.
        }
      })
      .catch(() => {
        // The notice is best-effort; the artifact dialog remains the fallback.
      })
    return () => {
      cancelled = true
    }
  }, [jobId, shouldCheck])

  const reason =
    result !== null && result.jobId === jobId ? result.reason : null
  if (!shouldCheck || reason === null) return null
  return (
    <div className={styles.notice} role="status">
      不适用 · 不可上传{reason ? `：${reason}` : ''}
    </div>
  )
}
