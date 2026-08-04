import { create } from 'zustand'
import type { ReactNode } from 'react'

export interface PageHeaderState {
  pageTitle: string | null
  pageSubtitle: ReactNode | null
  detailPageActions: ReactNode | null
  setPageTitle: (title: string | null) => void
  setPageSubtitle: (subtitle: ReactNode | null) => void
  setDetailPageActions: (actions: ReactNode | null) => void
}

export const usePageHeaderStore = create<PageHeaderState>((set) => ({
  pageTitle: null,
  pageSubtitle: null,
  detailPageActions: null,
  setPageTitle: (pageTitle) => set({ pageTitle }),
  setPageSubtitle: (pageSubtitle) => set({ pageSubtitle }),
  setDetailPageActions: (detailPageActions) => set({ detailPageActions }),
}))
