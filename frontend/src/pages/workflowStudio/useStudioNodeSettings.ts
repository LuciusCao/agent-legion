import { useEffect } from 'react'
import { useSettingStore } from '../../stores/settingStore'

// Load node config schemas/values into the setting store so the studio
// inspector can edit per-node configuration in context. Failures surface as
// the store's saveError and never block the studio itself.
export function useStudioNodeSettings(workspaceId: string | undefined) {
  useEffect(() => {
    if (!workspaceId) return
    const store = useSettingStore.getState()
    store.setWorkspaceId(workspaceId)
    void store.fetchSettings(workspaceId)
  }, [workspaceId])
}
