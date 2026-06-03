import { create } from 'zustand'
import { fetchPackages } from '../api'

interface PackageItem {
  id: number
  name: string
  path: string
  video_count: number
  size_bytes: number
  created_at: string
}

interface PackageState {
  packages: PackageItem[]
  loading: boolean
  fetchPackagesList: () => Promise<void>
  removePackage: (id: number) => void
  renamePackage: (id: number, name: string) => void
}

export const usePackageStore = create<PackageState>((set) => ({
  packages: [],
  loading: false,
  fetchPackagesList: async () => {
    set({ loading: true })
    try {
      const data = await fetchPackages()
      set({ packages: data.packages || [], loading: false })
    } catch {
      set({ packages: [], loading: false })
    }
  },
  removePackage: (id) => {
    set((state) => ({ packages: state.packages.filter((p) => p.id !== id) }))
  },
  renamePackage: (id, name) => {
    set((state) => ({
      packages: state.packages.map((p) => (p.id === id ? { ...p, name } : p)),
    }))
  },
}))
