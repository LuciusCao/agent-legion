import { memo } from 'react'
import { useJobStore } from '../../stores/jobStore'
import { JobListVirtualRow } from './JobListVirtualRow'

interface Props {
  jobId: string
  selected: boolean
  selectMode: boolean
  /** Row's translate offset in px — a plain number keeps the memo prop stable
   *  across virtualizer recalculations (VirtualItem is a new object per pass). */
  virtualRowStart: number
  workspaceId: string
  onToggleSelect: (jobId: string) => void
}

// Memoized per-row subscriber: each row subscribes to its own job object in
// the store, so an SSE patch batch only re-renders the rows whose job changed
// instead of the whole list.
export const JobListVirtualRowById = memo(function JobListVirtualRowById(
  props: Props
) {
  const job = useJobStore((state) => state.jobsById[props.jobId])
  if (!job) return null
  return <JobListVirtualRow {...props} job={job} />
})
