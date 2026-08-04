import { TokenUsagePanel } from './TokenUsagePanel'
import { TokenUsageJobPanel } from './TokenUsageJobPanel'

interface TokenUsageDialogContentProps {
  scope: 'workspace' | 'job'
  workspaceId?: string
  jobId?: string
}

export function TokenUsageDialogContent({
  scope,
  workspaceId,
  jobId,
}: TokenUsageDialogContentProps) {
  if (scope === 'workspace' && workspaceId)
    return <TokenUsagePanel workspaceId={workspaceId} />
  if (scope === 'job' && jobId) return <TokenUsageJobPanel jobId={jobId} />
  return null
}
