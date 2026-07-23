import { create } from 'zustand'
import { api, fetchPackages } from '../api'
import type { PackageItem } from '../types/packageTypes'

export type { PackageItem } from '../types/packageTypes'

interface PackageState {
  packages: PackageItem[]
  history: PackageItem[]
  selectedIds: number[]
  loading: boolean
  error: string | null
  fetchPackagesList: () => Promise<void>
  fetchPackages: () => Promise<void>
  fetchHistory: () => Promise<void>
  removePackage: (id: number) => void
  renamePackage: (id: number, name: string) => void
  toggleLock: (id: number, locked: boolean) => void
  toggleSelection: (id: number) => void
  selectAll: () => void
  clearSelection: () => void
  downloadUrlFor: (id: number) => string
}

export const usePackageStore = create<PackageState>((set) => ({
  packages: [],
  history: [],
  selectedIds: [],
  loading: false,
  error: null,

  fetchPackagesList: async () => {
    set({ loading: true, error: null })
    try {
      const data = await fetchPackages()
      set({ packages: data.packages || [], loading: false })
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err)
      set({ packages: [], loading: false, error: message })
    }
  },

  fetchPackages: async () => {
    set({ loading: true, error: null })
    try {
      const data = await fetchPackages()
      set({ packages: data.packages || [], loading: false })
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err)
      set({ packages: [], loading: false, error: message })
    }
  },

  fetchHistory: async () => {
    set({ loading: true, error: null })
    try {
      const data = await api<{ packages: PackageItem[] }>(
        '/api/packages/history'
      )
      set({ history: data.packages || [], loading: false })
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err)
      set({ history: [], loading: false, error: message })
    }
  },

  removePackage: (id) => {
    set((state) => ({
      packages: state.packages.filter((p) => p.id !== id),
      selectedIds: state.selectedIds.filter((sid) => sid !== id),
    }))
  },

  renamePackage: (id, name) => {
    set((state) => ({
      packages: state.packages.map((p) => (p.id === id ? { ...p, name } : p)),
    }))
  },

  toggleLock: (id, locked) => {
    set((state) => ({
      packages: state.packages.map((p) =>
        p.id === id ? { ...p, locked: locked ? 1 : 0 } : p
      ),
    }))
  },

  toggleSelection: (id) => {
    set((state) => {
      const selected = new Set(state.selectedIds)
      if (selected.has(id)) {
        selected.delete(id)
      } else {
        selected.add(id)
      }
      return { selectedIds: Array.from(selected) }
    })
  },

  selectAll: () => {
    set((state) => ({ selectedIds: state.packages.map((p) => p.id) }))
  },

  clearSelection: () => set({ selectedIds: [] }),

  downloadUrlFor: (id) => `/api/packages/${id}/download`,
}))
