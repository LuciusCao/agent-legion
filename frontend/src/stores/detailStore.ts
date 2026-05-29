import { create } from 'zustand'
import type { VideoItem, DetailTab, PhaseRun, TranscriptionRun } from '../types'
import { api } from '../api'

interface DetailState {
  currentVideo: VideoItem | null
  log: string
  phaseRuns: PhaseRun[]
  transcriptionRuns: TranscriptionRun[]
  activeTab: DetailTab
  isLoading: boolean
  error: string | null
  loadVideo: (id: string) => Promise<void>
  loadLog: (id: string) => Promise<void>
  loadPhaseRuns: (id: string) => Promise<void>
  updatePhaseRuns: (
    phaseRuns: PhaseRun[],
    transcriptionRuns: TranscriptionRun[],
    video?: VideoItem
  ) => void
  setActiveTab: (tab: DetailTab) => void
}

export const useDetailStore = create<DetailState>((set) => ({
  currentVideo: null,
  log: '',
  phaseRuns: [],
  transcriptionRuns: [],
  activeTab: 'nodes',
  isLoading: false,
  error: null,

  loadVideo: async (id) => {
    set({ isLoading: true, error: null })
    try {
      const data = await api<{
        video: VideoItem
        phase_runs: PhaseRun[]
        transcription_runs: TranscriptionRun[]
      }>(`/api/videos/${id}`)
      const video = data.video || null
      set({
        currentVideo: video,
        phaseRuns: data.phase_runs || [],
        transcriptionRuns: data.transcription_runs || [],
      })
      if (video) {
        set({ activeTab: 'subtitles' })
      }
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err)
      set({ error: message })
    } finally {
      set({ isLoading: false })
    }
  },

  loadLog: async (id) => {
    try {
      const data = await api<{ log: string }>(`/api/videos/${id}/logs`)
      set({ log: data.log || '暂无日志' })
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err)
      set({ log: '加载日志失败', error: message })
    }
  },

  loadPhaseRuns: async (id) => {
    try {
      const data = await api<{
        phase_runs: PhaseRun[]
        transcription_runs: TranscriptionRun[]
      }>(`/api/videos/${id}`)
      set({
        phaseRuns: data.phase_runs || [],
        transcriptionRuns: data.transcription_runs || [],
      })
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err)
      set({ error: message })
    }
  },

  updatePhaseRuns: (phaseRuns, transcriptionRuns, video) => {
    set({
      phaseRuns,
      transcriptionRuns,
      ...(video ? { currentVideo: video } : {}),
    })
  },

  setActiveTab: (tab) => set({ activeTab: tab }),
}))
