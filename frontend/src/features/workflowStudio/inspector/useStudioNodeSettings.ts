import { useSettingStoreHydration } from '../../../hooks/useWorkspaceSettingsQuery'

// Load node config schemas/values into the setting store so the studio
// inspector can edit per-node configuration in context. Failures surface as
// the store's saveError and never block the studio itself.
export function useStudioNodeSettings(workspaceId: string | undefined) {
  useSettingStoreHydration(workspaceId)
}
