import { create } from 'zustand'
import type { ReactNode } from 'react'

interface Toast {
  message: string
  type: 'success' | 'error'
}

export interface UiState {
  workspacePackageDialogOpen: boolean
  tokenUsageDialogOpen: boolean
  toast: Toast | null
  pageTitle: string | null
  pageSubtitle: ReactNode | null
  detailPageActions: ReactNode | null
  setWorkspacePackageDialogOpen: (open: boolean) => void
  setTokenUsageDialogOpen: (open: boolean) => void
  showToast: (message: string, type: 'success' | 'error') => void
  clearToast: () => void
  setPageTitle: (title: string | null) => void
  setPageSubtitle: (subtitle: ReactNode | null) => void
  setDetailPageActions: (actions: ReactNode | null) => void
}

export const useUiStore = create<UiState>((set) => ({
  workspacePackageDialogOpen: false,
  tokenUsageDialogOpen: false,
  toast: null,
  pageTitle: null,
  pageSubtitle: null,
  detailPageActions: null,

  setWorkspacePackageDialogOpen: (open) =>
    set({ workspacePackageDialogOpen: open }),
  setTokenUsageDialogOpen: (open) => set({ tokenUsageDialogOpen: open }),
  showToast: (message, type) => set({ toast: { message, type } }),
  clearToast: () => set({ toast: null }),
  setPageTitle: (pageTitle) => set({ pageTitle }),
  setPageSubtitle: (pageSubtitle) => set({ pageSubtitle }),
  setDetailPageActions: (detailPageActions) => set({ detailPageActions }),
}))
