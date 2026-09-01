import { useState } from 'react'
import { IconButton } from '@mui/material'
import { MaterialIcon } from '../MaterialIcon'
import type { JobSummary } from '../../types/jobTypes'
import { JobDiagnosisDialog } from '../../features/jobDiagnosis/JobDiagnosisDialog'
import styles from './JobListItemDiagnosis.module.css'

/** 失败 job 的排查入口（#329）：列表行内图标按钮 + 诊断弹窗，弹窗只在
 * 打开时挂载（每次打开 = 全新排查会话）。无 workspaceId（调用方未绑定
 * workspace 上下文）或非失败 job 时不渲染。 */
export function JobListItemDiagnosis({
  job,
  workspaceId,
}: {
  job: JobSummary
  workspaceId?: string
}) {
  const [open, setOpen] = useState(false)
  if (job.status !== 'failed' || !workspaceId) return null
  const nodeLabel =
    (job.node_summaries ?? []).find((n) => n.status === 'failed')?.label ?? null
  return (
    <>
      <IconButton
        aria-label="排查"
        size="small"
        className={styles.diagnoseButton}
        onClick={(event) => {
          event.stopPropagation()
          setOpen(true)
        }}
      >
        <MaterialIcon name="smart_toy" sx={{ fontSize: 18 }} />
      </IconButton>
      {open && (
        <JobDiagnosisDialog
          open
          target={{
            workspaceId,
            jobId: job.id,
            jobTitle: job.title || job.source_id,
            nodeKey: job.active_node_key ?? null,
            nodeLabel,
          }}
          onClose={() => setOpen(false)}
        />
      )}
    </>
  )
}
