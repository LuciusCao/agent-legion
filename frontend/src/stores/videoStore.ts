import { create } from 'zustand'
import type { VideoItem, ContentType, RunToResult } from '../types'
import { api } from '../api'
import { filterVideos } from '../helpers'

interface VideoState {
  videos: VideoItem[]
  selectedType: ContentType
  statusFilter: string
  searchQuery: string
  packedFilter: 'all' | 'packed' | 'unpacked'
  selectMode: boolean
  packageSelectMode: boolean
  selectedIds: Set<string>
  isLoading: boolean
  sseConnected: boolean
  error: string | null
  fetchVideos: () => Promise<void>
  clearError: () => void
  mergeVideo: (video: VideoItem) => void
  removeVideo: (videoId: string) => void
  setSelectedType: (type: ContentType) => void
  setStatusFilter: (status: string) => void
  setSearchQuery: (query: string) => void
  setPackedFilter: (filter: 'all' | 'packed' | 'unpacked') => void
  toggleSelectMode: () => void
  exitSelectMode: () => void
  togglePackageSelectMode: () => void
  toggleVideoSelection: (id: string) => void
  selectAllVisible: () => void
  selectPackageAll: () => void
  selectPackageApproved: () => void
  selectPackageUnpacked: () => void
  clearSelection: () => void
  setSseConnected: (connected: boolean) => void
  batchDelete: (ids: string[]) => Promise<{
    results: Array<{ video_id: string; status: string; message?: string }>
  }>
  batchRerun: (
    ids: string[],
    phase: string
  ) => Promise<{
    results: Array<{ video_id: string; status: string; message?: string }>
  }>
  runTo: (
    id: string,
    targetPhase: string,
    startPhase?: string | null
  ) => Promise<{ result: RunToResult; video: VideoItem | null }>
  batchRunTo: (
    ids: string[],
    targetPhase: string,
    startPhase?: string | null
  ) => Promise<{ results: RunToResult[] }>
  batchPackage: (
    ids: string[]
  ) => Promise<{ path: string; download_url: string }>
}

function hasAllApprovedInteractions(video: VideoItem): boolean {
  return video.interaction_review_status === 'all_passed'
}

export const useVideoStore = create<VideoState>((set, get) => ({
  videos: [],
  selectedType: 'knowledge',
  statusFilter: 'all',
  searchQuery: '',
  packedFilter: 'all',
  selectMode: false,
  packageSelectMode: false,
  selectedIds: new Set(),
  isLoading: false,
  sseConnected: true,
  error: null,

  fetchVideos: async () => {
    set({ isLoading: true, error: null })
    try {
      const data = await api<{ videos: VideoItem[] }>('/api/videos')
      set({ videos: data.videos, error: null })
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err)
      set({ error: message })
    } finally {
      set({ isLoading: false })
    }
  },

  clearError: () => set({ error: null }),

  mergeVideo: (video) => {
    set((state) => {
      const index = state.videos.findIndex((v) => v.id === video.id)
      if (index >= 0) {
        const next = [...state.videos]
        next[index] = video
        return { videos: next }
      }
      return { videos: [video, ...state.videos] }
    })
  },

  removeVideo: (videoId) => {
    set((state) => ({
      videos: state.videos.filter((v) => v.id !== videoId),
      selectedIds: new Set(
        [...state.selectedIds].filter((id) => id !== videoId)
      ),
    }))
  },

  setSelectedType: (type) => {
    set({ selectedType: type, selectedIds: new Set(), searchQuery: '' })
  },

  setStatusFilter: (status) => {
    set({
      statusFilter: status,
      selectedIds: new Set(),
      ...(status !== 'completed' ? { packedFilter: 'all' } : {}),
    })
  },

  setSearchQuery: (query) => {
    set({ searchQuery: query, selectedIds: new Set() })
  },

  setPackedFilter: (filter) => {
    set({ packedFilter: filter, selectedIds: new Set() })
  },

  toggleSelectMode: () => {
    set((state) => ({
      selectMode: !state.selectMode,
      packageSelectMode: false,
      selectedIds: new Set(),
    }))
  },

  exitSelectMode: () =>
    set({
      selectMode: false,
      packageSelectMode: false,
      selectedIds: new Set(),
    }),

  togglePackageSelectMode: () =>
    set((state) => ({
      packageSelectMode: !state.packageSelectMode,
      selectMode: false,
      selectedIds: new Set(),
    })),

  selectPackageAll: () =>
    set((state) => {
      const visible = filterVideos(state.videos, {
        selectedType: state.selectedType,
        statusFilter: state.statusFilter,
        searchQuery: state.searchQuery,
        packedFilter: state.packedFilter,
      })
      const completed = visible.filter((v) => v.status === 'completed')
      return { selectedIds: new Set(completed.map((v) => v.id)) }
    }),

  selectPackageUnpacked: () =>
    set((state) => {
      const unpacked = state.videos.filter(
        (v) => v.status === 'completed' && !v.packed
      )
      return { selectedIds: new Set(unpacked.map((v) => v.id)) }
    }),

  selectPackageApproved: () =>
    set((state) => {
      const visible = filterVideos(state.videos, {
        selectedType: state.selectedType,
        statusFilter: state.statusFilter,
        searchQuery: state.searchQuery,
        packedFilter: state.packedFilter,
      })
      const approved = visible.filter(
        (v) => v.status === 'completed' && hasAllApprovedInteractions(v)
      )
      return { selectedIds: new Set(approved.map((v) => v.id)) }
    }),

  toggleVideoSelection: (id) => {
    set((state) => {
      const next = new Set(state.selectedIds)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return { selectedIds: next }
    })
  },

  selectAllVisible: () => {
    set((state) => {
      const filtered = filterVideos(state.videos, {
        selectedType: state.selectedType,
        statusFilter: state.statusFilter,
        searchQuery: state.searchQuery,
        packedFilter: state.packedFilter,
      })
      return { selectedIds: new Set(filtered.map((v) => v.id)) }
    })
  },

  clearSelection: () => set({ selectedIds: new Set() }),
  setSseConnected: (connected) => set({ sseConnected: connected }),

  batchDelete: async (ids) => {
    return api('/api/videos/batch/delete', {
      method: 'POST',
      body: JSON.stringify({ video_ids: ids }),
    })
  },

  batchRerun: async (ids, phase) => {
    return api('/api/videos/batch/rerun', {
      method: 'POST',
      body: JSON.stringify({ video_ids: ids, phase }),
    })
  },

  runTo: async (id, targetPhase, startPhase = null) => {
    return api(`/api/videos/${id}/run-to`, {
      method: 'POST',
      body: JSON.stringify({
        target_phase: targetPhase,
        start_phase: startPhase,
      }),
    })
  },

  batchRunTo: async (ids, targetPhase, startPhase = null) => {
    return api('/api/videos/batch/run-to', {
      method: 'POST',
      body: JSON.stringify({
        video_ids: ids,
        target_phase: targetPhase,
        start_phase: startPhase,
      }),
    })
  },

  batchPackage: async (ids) => {
    const result = await api<{ path: string; download_url: string }>(
      '/api/package',
      {
        method: 'POST',
        body: JSON.stringify({ video_ids: ids }),
      }
    )
    await get().fetchVideos()
    return result
  },
}))
