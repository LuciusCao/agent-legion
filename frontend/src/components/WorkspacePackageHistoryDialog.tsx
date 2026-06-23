import { useUiStore } from '../stores/uiStore'
import { PackageHistoryDialog } from './PackageHistoryDialog'

interface Props {
  workspaceId: string
}

export function WorkspacePackageHistoryDialog({ workspaceId }: Props) {
  const { workspacePackageDialogOpen, setWorkspacePackageDialogOpen } =
    useUiStore()
  return (
    <PackageHistoryDialog
      open={workspacePackageDialogOpen}
      onClose={() => setWorkspacePackageDialogOpen(false)}
      scope="workspace"
      workspaceId={workspaceId}
    />
  )
}
