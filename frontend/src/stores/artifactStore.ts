import { create } from 'zustand'
import type { VideoArtifacts } from '../types'
import { api } from '../api'

interface ArtifactState {
  artifacts: VideoArtifacts
  loadArtifacts: (id: string) => Promise<void>
  resetArtifacts: () => void
}

const emptyArtifacts: VideoArtifacts = {
  subtitles: [],
  chapters: [],
  interactions: [],
  metadata: null,
  review: null,
  checklist: null,
}

export const useArtifactStore = create<ArtifactState>((set) => ({
  artifacts: emptyArtifacts,
  loadArtifacts: async (id) => {
    try {
      const data = await api<{
        subtitles?: Array<{
          index: number
          start: number
          end: number
          text: string
        }>
        chapters?: Array<{
          id?: string
          start_time?: number
          start?: number
          end_time?: number
          end?: number
          title: string
        }>
        interactions?: Array<Record<string, unknown>>
        metadata?: Record<string, unknown> | null
        review?: Record<string, unknown> | null
        checklist?: Record<string, unknown> | null
      }>(`/api/videos/${id}/artifacts`)
      set({
        artifacts: {
          subtitles: data.subtitles || [],
          chapters: (data.chapters || []).map((c) => ({
            id: c.id,
            start: c.start_time ?? c.start ?? 0,
            end: c.end_time ?? c.end ?? 0,
            title: c.title,
          })),
          interactions: (data.interactions ||
            []) as VideoArtifacts['interactions'],
          metadata: data.metadata || null,
          review: data.review || null,
          checklist: data.checklist || null,
        },
      })
    } catch {
      set({ artifacts: emptyArtifacts })
    }
  },
  resetArtifacts: () => set({ artifacts: emptyArtifacts }),
}))
