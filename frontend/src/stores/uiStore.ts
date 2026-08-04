import { create } from 'zustand'
import type { ContentType } from '../types'

interface Toast {
  message: string
  type: 'success' | 'error'
}

export interface UiState {
  addDialogOpen: boolean
  addContentType: ContentType
  addDialogContext: 'video' | 'workspace'
  addDialogWorkspaceId: string | undefined
  workspacePackageDialogOpen: boolean
  tokenUsageDialogOpen: boolean
  toast: Toast | null
  openAddDialog: (opts?: {
    context?: 'video' | 'workspace'
    workspaceId?: string
  }) => void
  closeAddDialog: () => void
  setAddContentType: (type: ContentType) => void
  setWorkspacePackageDialogOpen: (open: boolean) => void
  setTokenUsageDialogOpen: (open: boolean) => void
  showToast: (message: string, type: 'success' | 'error') => void
  clearToast: () => void
}

export const useUiStore = create<UiState>((set) => ({
  addDialogOpen: false,
  addContentType: 'knowledge',
  addDialogContext: 'workspace',
  addDialogWorkspaceId: undefined,
  workspacePackageDialogOpen: false,
  tokenUsageDialogOpen: false,
  toast: null,

  openAddDialog: (opts) =>
    set({
      addDialogOpen: true,
      addContentType: 'knowledge',
      addDialogContext: opts?.context || 'workspace',
      addDialogWorkspaceId: opts?.workspaceId,
    }),
  closeAddDialog: () => set({ addDialogOpen: false }),
  setAddContentType: (type) => set({ addContentType: type }),
  setWorkspacePackageDialogOpen: (open) =>
    set({ workspacePackageDialogOpen: open }),
  setTokenUsageDialogOpen: (open) => set({ tokenUsageDialogOpen: open }),
  showToast: (message, type) => set({ toast: { message, type } }),
  clearToast: () => set({ toast: null }),
}))
