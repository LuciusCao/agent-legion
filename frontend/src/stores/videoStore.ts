import { create } from 'zustand'
import type { VideoItem, ContentType, RunToResult } from '../types'
import { api } from '../api'
import { filterVideos, statusGroup } from '../helpers'

interface VideoState {
  videos: VideoItem[]
  selectedType: ContentType
  statusFilter: string
  searchQuery: string
  packedFilter: 'all' | 'packed' | 'unpacked'
  selectMode: boolean
  selectedIds: Set<string>
  isLoading: boolean
  sseConnected: boolean
  error: string | null
  _filteredVideos: VideoItem[]
  _counts: Record<string, number>
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
  toggleVideoSelection: (id: string) => void
  selectAllVisible: () => void
  selectUnpacked: () => void
  selectReviewApproved: () => void
  selectReviewNotPassed: () => void
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
  batchPackage: (ids: string[]) => Promise<{ accepted: boolean }>
}

function hasAllApprovedInteractions(video: VideoItem): boolean {
  return video.interaction_review_status === 'all_passed'
}

export const useVideoStore = create<VideoState>((set) => ({
  videos: [],
  selectedType: 'knowledge',
  statusFilter: 'all',
  searchQuery: '',
  packedFilter: 'all',
  selectMode: false,
  selectedIds: new Set(),
  isLoading: false,
  sseConnected: true,
  error: null,
  _filteredVideos: [],
  _counts: { all: 0, queued: 0, running: 0, failed: 0, completed: 0, packed: 0, unpacked: 0 },

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
      selectedIds: new Set(),
    }))
  },

  exitSelectMode: () =>
    set({
      selectMode: false,
      selectedIds: new Set(),
    }),

  selectUnpacked: () =>
    set((state) => {
      const visible = filterVideos(state.videos, {
        selectedType: state.selectedType,
        statusFilter: state.statusFilter,
        searchQuery: state.searchQuery,
        packedFilter: state.packedFilter,
      })
      const unpacked = visible.filter(
        (v) => v.status === 'completed' && !v.packed
      )
      return { selectedIds: new Set(unpacked.map((v) => v.id)) }
    }),

  selectReviewApproved: () =>
    set((state) => {
      const visible = filterVideos(state.videos, {
        selectedType: state.selectedType,
        statusFilter: state.statusFilter,
        searchQuery: state.searchQuery,
        packedFilter: state.packedFilter,
      })
      const approved = visible.filter(
        (v) =>
          v.content_type === 'knowledge' &&
          v.status === 'completed' &&
          hasAllApprovedInteractions(v)
      )
      return { selectedIds: new Set(approved.map((v) => v.id)) }
    }),

  selectReviewNotPassed: () =>
    set((state) => {
      const visible = filterVideos(state.videos, {
        selectedType: state.selectedType,
        statusFilter: state.statusFilter,
        searchQuery: state.searchQuery,
        packedFilter: state.packedFilter,
      })
      const notPassed = visible.filter(
        (v) =>
          v.content_type === 'knowledge' &&
          v.status === 'completed' &&
          (v.interaction_review_status === 'partial' ||
            v.interaction_review_status === 'all_failed' ||
            !v.interaction_review_status)
      )
      return { selectedIds: new Set(notPassed.map((v) => v.id)) }
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
    try {
      return await api('/api/videos/batch/delete', {
        method: 'POST',
        body: JSON.stringify({ video_ids: ids }),
      })
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err)
      set({ error: message })
      throw err
    }
  },

  batchRerun: async (ids, phase) => {
    try {
      return await api('/api/videos/batch/rerun', {
        method: 'POST',
        body: JSON.stringify({ video_ids: ids, phase }),
      })
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err)
      set({ error: message })
      throw err
    }
  },

  runTo: async (id, targetPhase, startPhase = null) => {
    try {
      return await api(`/api/videos/${id}/run-to`, {
        method: 'POST',
        body: JSON.stringify({
          target_phase: targetPhase,
          start_phase: startPhase,
        }),
      })
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err)
      set({ error: message })
      throw err
    }
  },

  batchRunTo: async (ids, targetPhase, startPhase = null) => {
    try {
      return await api('/api/videos/batch/run-to', {
        method: 'POST',
        body: JSON.stringify({
          video_ids: ids,
          target_phase: targetPhase,
          start_phase: startPhase,
        }),
      })
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err)
      set({ error: message })
      throw err
    }
  },

  batchPackage: async (ids) => {
    try {
      const result = await api<{ accepted: boolean }>('/api/package', {
        method: 'POST',
        body: JSON.stringify({ video_ids: ids }),
      })
      return result
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err)
      set({ error: message })
      throw err
    }
  },
}))

const STATUSES = ['queued', 'running', 'failed', 'completed']

function shallowEqual(a: Record<string, unknown>, b: Record<string, unknown>): boolean {
  const keysA = Object.keys(a)
  const keysB = Object.keys(b)
  if (keysA.length !== keysB.length) return false
  return keysA.every((k) => a[k] === b[k])
}

useVideoStore.subscribe((state, prevState) => {
  const filterChanged =
    state.videos !== prevState.videos ||
    state.selectedType !== prevState.selectedType ||
    state.statusFilter !== prevState.statusFilter ||
    state.searchQuery !== prevState.searchQuery ||
    state.packedFilter !== prevState.packedFilter

  if (!filterChanged) return

  const filtered = filterVideos(state.videos, {
    selectedType: state.selectedType,
    statusFilter: state.statusFilter,
    searchQuery: state.searchQuery,
    packedFilter: state.packedFilter,
  })

  const counts: Record<string, number> = { all: state.videos.length }
  STATUSES.forEach((s) => {
    counts[s] = state.videos.filter((v) => statusGroup(v) === s).length
  })
  counts.packed = state.videos.filter(
    (v) => v.status === 'completed' && v.packed
  ).length
  counts.unpacked = state.videos.filter(
    (v) => v.status === 'completed' && !v.packed
  ).length

  queueMicrotask(() => {
    const current = useVideoStore.getState()
    if (
      current._filteredVideos !== filtered ||
      !shallowEqual(current._counts, counts)
    ) {
      useVideoStore.setState({ _filteredVideos: filtered, _counts: counts })
    }
  })
})
