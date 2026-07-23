import { create } from 'zustand'
import type { VideoArtifacts } from '../types'

interface ArtifactState {
  artifacts: VideoArtifacts
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
  resetArtifacts: () => set({ artifacts: emptyArtifacts }),
}))
