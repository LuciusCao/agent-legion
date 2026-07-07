import type { VirtualItem } from '@tanstack/react-virtual'
import { useJobStore } from '../stores/jobStore'
import { JobListVirtualRow } from './JobListVirtualRow'

interface Props {
  jobId: string
  selected: boolean
  selectMode: boolean
  virtualRow: VirtualItem
  workspaceId: string
  onToggleSelect: () => void
}

export function JobListVirtualRowById(props: Props) {
  const job = useJobStore((state) => state.jobsById[props.jobId])
  if (!job) return null
  return <JobListVirtualRow {...props} job={job} />
}
