import {
  computeDirty,
  type HydrateSettingsInput,
  type SettingStoreSet,
} from '../state'

export function loadActions(set: SettingStoreSet) {
  return {
    // 由 useSettingStoreHydration 在快照到达时调用：同时写 draft 与
    // original* 基准并重算 isDirty（对齐原 fetchSettings 的 set 逻辑）。
    // 调用方负责判断水合时机（切换工作区强制重置 / 非 dirty 才同步）。
    hydrateSettings(workspaceId: string, snapshot: HydrateSettingsInput) {
      set((state) => {
        const nextState = {
          ...state,
          workspaceId,
          saveError: null,
          workspaceName: snapshot.workspaceName,
          workspaceDescription: snapshot.workspaceDescription,
          originalWorkspaceName: snapshot.workspaceName,
          originalWorkspaceDescription: snapshot.workspaceDescription,
          settings: snapshot.settings,
          originalSettings: snapshot.settings,
          executionConfiguration: snapshot.executionConfiguration,
          originalExecutionConfiguration: snapshot.executionConfiguration,
        }
        return { ...nextState, isDirty: computeDirty(nextState) }
      })
    },
  }
}
