import { useParams } from 'react-router-dom'
import type { TaskCardModel } from './taskCardModel'
import { TaskCardGate } from './TaskCardGate'
import { RunSharedResourceUsage } from '../sharedResources/SharedResourceUsage'

export function TaskCardRunGate({
  card,
  workspacePaused,
}: {
  card: TaskCardModel
  workspacePaused: boolean
}) {
  const { workspaceId = '' } = useParams<{ workspaceId: string }>()
  return (
    <>
      <TaskCardGate card={card} workspacePaused={workspacePaused} />
      {card.kind === 'run-batch' && workspaceId && (
        <RunSharedResourceUsage workspaceId={workspaceId} runId={card.id} />
      )}
    </>
  )
}
