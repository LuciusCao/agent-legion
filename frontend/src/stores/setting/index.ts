import { create } from 'zustand'
import {
  computeDirty,
  defaultExecutionConfiguration,
  defaultSettings,
  type SettingState,
} from './state'
import { loadActions } from './actions/loadActions'
import { saveActions } from './actions/saveActions'
import { executionConfigActions } from './actions/executionConfigActions'

export const useSettingStore = create<SettingState>((set, get) => ({
  workspaceId: null,
  workspaceName: '',
  workspaceDescription: '',
  settings: defaultSettings,
  originalWorkspaceName: '',
  originalWorkspaceDescription: '',
  originalSettings: null,
  isDirty: false,
  isSaving: false,
  saveError: null,
  executionConfiguration: defaultExecutionConfiguration,
  originalExecutionConfiguration: null,

  setWorkspaceId(id) {
    set({ workspaceId: id })
  },

  setWorkspaceName(name) {
    set((state) => {
      const nextState = { ...state, workspaceName: name }
      return { ...nextState, isDirty: computeDirty(nextState) }
    })
  },

  setWorkspaceDescription(description) {
    set((state) => {
      const nextState = { ...state, workspaceDescription: description }
      return { ...nextState, isDirty: computeDirty(nextState) }
    })
  },

  setSettings(s) {
    set((state) => {
      // workflowKey 已从快照 blob 退役（#211 Phase 2 第二批）：key 与
      // workspace id 绑定且不可变，settings 编辑面只剩 entityType/
      // previewHidden，不再存在「换 workflow 清空节点限制」的分支。
      const nextSettings = { ...state.settings, ...s }
      const nextState = {
        ...state,
        settings: nextSettings,
      }
      return { ...nextState, isDirty: computeDirty(nextState) }
    })
  },

  ...loadActions(set),
  ...saveActions(set, get),
  ...executionConfigActions(set),
}))
