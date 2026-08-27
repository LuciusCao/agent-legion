import { AppBar } from '../../components/AppBar'
import { WorkflowStudioCommandBarContainer } from './WorkflowStudioCommandBarContainer'
import { useWorkflowStudioAppTitle } from './useWorkflowStudioAppTitle'

type Props = {
  workspaceId: string | undefined
  scrolled?: boolean
}

export function WorkflowStudioAppBar({ workspaceId, scrolled }: Props) {
  const title = useWorkflowStudioAppTitle(workspaceId)
  return (
    <AppBar
      title={title}
      backTo={workspaceId ? `/workspaces/${workspaceId}` : '/'}
      scrolled={scrolled}
      rightActions={<WorkflowStudioCommandBarContainer />}
    />
  )
}
