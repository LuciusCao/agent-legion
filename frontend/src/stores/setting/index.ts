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
      const nextSettings = { ...state.settings, ...s }
      const workflowChanged =
        s.workflowKey !== undefined &&
        s.workflowKey !== state.settings.workflowKey
      const nextExecutionConfiguration = workflowChanged
        ? {
            ...state.executionConfiguration,
            node_limits: [],
          }
        : state.executionConfiguration
      const nextState = {
        ...state,
        settings: nextSettings,
        executionConfiguration: nextExecutionConfiguration,
      }
      return { ...nextState, isDirty: computeDirty(nextState) }
    })
  },

  ...loadActions(set),
  ...saveActions(set, get),
  ...executionConfigActions(set),
}))
