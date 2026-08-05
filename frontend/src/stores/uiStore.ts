import { create } from 'zustand'
import type { ReactNode } from 'react'
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
  pageTitle: string | null
  pageSubtitle: ReactNode | null
  detailPageActions: ReactNode | null
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
  setPageTitle: (title: string | null) => void
  setPageSubtitle: (subtitle: ReactNode | null) => void
  setDetailPageActions: (actions: ReactNode | null) => void
}

export const useUiStore = create<UiState>((set) => ({
  addDialogOpen: false,
  addContentType: 'knowledge',
  addDialogContext: 'workspace',
  addDialogWorkspaceId: undefined,
  workspacePackageDialogOpen: false,
  tokenUsageDialogOpen: false,
  toast: null,
  pageTitle: null,
  pageSubtitle: null,
  detailPageActions: null,

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
  setPageTitle: (pageTitle) => set({ pageTitle }),
  setPageSubtitle: (pageSubtitle) => set({ pageSubtitle }),
  setDetailPageActions: (detailPageActions) => set({ detailPageActions }),
}))
