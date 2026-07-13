import { AppBar } from '../../components/AppBar'
import { WorkflowStudioCommandBarContainer } from './WorkflowStudioCommandBarContainer'
import type { useWorkflowStudio } from './useWorkflowStudio'
import { useWorkflowStudioAppTitle } from './useWorkflowStudioAppTitle'
type Studio = ReturnType<typeof useWorkflowStudio>

type Props = {
  workspaceId: string | undefined
  studio: Studio
  scrolled?: boolean
  onOpenChanges: () => void
  onOpenYaml: () => void
  onValidate: () => void
}

export function WorkflowStudioAppBar({
  workspaceId,
  studio,
  scrolled,
  onOpenChanges,
  onOpenYaml,
  onValidate,
}: Props) {
  const title = useWorkflowStudioAppTitle(workspaceId)
  return (
    <AppBar
      title={title}
      backTo={workspaceId ? `/workspaces/${workspaceId}` : '/'}
      scrolled={scrolled}
      rightActions={
        <WorkflowStudioCommandBarContainer
          studio={studio}
          onOpenChanges={onOpenChanges}
          onOpenYaml={onOpenYaml}
          onValidate={onValidate}
        />
      }
    />
  )
}
