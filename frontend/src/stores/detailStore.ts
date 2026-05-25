import { create } from "zustand";
import type { VideoItem, VideoArtifacts, DetailTab, PhaseRun, TranscriptionRun } from "../types";
import { api } from "../api";

interface DetailState {
  currentVideo: VideoItem | null;
  artifacts: VideoArtifacts;
  log: string;
  phaseRuns: PhaseRun[];
  transcriptionRuns: TranscriptionRun[];
  activeTab: DetailTab;
  triggeredNodeIndexes: Set<number>;
  dismissedNodeIndexes: Set<number>;
  currentSentence: string[];
  isLoading: boolean;
  loadVideo: (id: string) => Promise<void>;
  loadArtifacts: (id: string) => Promise<void>;
  loadLog: (id: string) => Promise<void>;
  loadPhaseRuns: (id: string) => Promise<void>;
  updatePhaseRuns: (phaseRuns: PhaseRun[], transcriptionRuns: TranscriptionRun[]) => void;
  setActiveTab: (tab: DetailTab) => void;
  triggerInteraction: (index: number) => void;
  dismissInteraction: (index: number) => void;
  replayInteraction: (index: number) => void;
  resetSentence: () => void;
  pushWord: (word: string) => void;
  clearSentence: () => void;
}

const emptyArtifacts: VideoArtifacts = {
  subtitles: [],
  chapters: [],
  interactions: [],
  metadata: null,
  review: null,
  checklist: null,
};

export const useDetailStore = create<DetailState>((set, _get) => ({
  currentVideo: null,
  artifacts: emptyArtifacts,
  log: "",
  phaseRuns: [],
  transcriptionRuns: [],
  activeTab: "nodes",
  triggeredNodeIndexes: new Set(),
  dismissedNodeIndexes: new Set(),
  currentSentence: [],
  isLoading: false,

  loadVideo: async (id) => {
    set({ isLoading: true });
    try {
      const data = await api<{ video: VideoItem; phase_runs: PhaseRun[]; transcription_runs: TranscriptionRun[] }>(`/api/videos/${id}`);
      const video = data.video || null;
      set({
        currentVideo: video,
        phaseRuns: data.phase_runs || [],
        transcriptionRuns: data.transcription_runs || [],
        triggeredNodeIndexes: new Set(),
        dismissedNodeIndexes: new Set(),
        currentSentence: [],
      });
      if (video) {
        set({ activeTab: video.content_type === "question" ? "subtitles" : "nodes" });
      }
    } finally {
      set({ isLoading: false });
    }
  },

  loadArtifacts: async (id) => {
    try {
      const data = await api<{
        subtitles?: Array<{ index: number; start: number; end: number; text: string }>;
        chapters?: Array<{ id?: string; start_time?: number; start?: number; end_time?: number; end?: number; title: string }>;
        interactions?: Array<Record<string, unknown>>;
        metadata?: Record<string, unknown> | null;
        review?: Record<string, unknown> | null;
        checklist?: Record<string, unknown> | null;
      }>(`/api/videos/${id}/artifacts`);
      set({
        artifacts: {
          subtitles: data.subtitles || [],
          chapters: (data.chapters || []).map((c) => ({
            id: c.id,
            start: c.start_time ?? c.start ?? 0,
            end: c.end_time ?? c.end ?? 0,
            title: c.title,
          })),
          interactions: (data.interactions || []) as VideoArtifacts["interactions"],
          metadata: data.metadata || null,
          review: data.review || null,
          checklist: data.checklist || null,
        },
      });
    } catch {
      set({ artifacts: emptyArtifacts });
    }
  },

  loadLog: async (id) => {
    try {
      const data = await api<{ log: string }>(`/api/videos/${id}/logs`);
      set({ log: data.log || "暂无日志" });
    } catch {
      set({ log: "加载日志失败" });
    }
  },

  loadPhaseRuns: async (id) => {
    try {
      const data = await api<{ phase_runs: PhaseRun[]; transcription_runs: TranscriptionRun[] }>(`/api/videos/${id}`);
      set({ phaseRuns: data.phase_runs || [], transcriptionRuns: data.transcription_runs || [] });
    } catch {
      // ignore
    }
  },

  updatePhaseRuns: (phaseRuns, transcriptionRuns) => {
    set({ phaseRuns, transcriptionRuns });
  },

  setActiveTab: (tab) => set({ activeTab: tab }),

  triggerInteraction: (index) => {
    set((state) => {
      const next = new Set(state.triggeredNodeIndexes);
      next.add(index);
      return { triggeredNodeIndexes: next };
    });
  },

  dismissInteraction: (index) => {
    set((state) => {
      const nextTriggered = new Set(state.triggeredNodeIndexes);
      nextTriggered.delete(index);
      const nextDismissed = new Set(state.dismissedNodeIndexes);
      nextDismissed.add(index);
      return { triggeredNodeIndexes: nextTriggered, dismissedNodeIndexes: nextDismissed };
    });
  },

  replayInteraction: (index) => {
    set((state) => {
      const nextTriggered = new Set(state.triggeredNodeIndexes);
      const nextDismissed = new Set(state.dismissedNodeIndexes);
      nextTriggered.delete(index);
      nextDismissed.delete(index);
      nextTriggered.add(index);
      return {
        triggeredNodeIndexes: nextTriggered,
        dismissedNodeIndexes: nextDismissed,
        currentSentence: [],
      };
    });
  },

  resetSentence: () => set({ currentSentence: [] }),

  pushWord: (word) => {
    set((state) => ({ currentSentence: [...state.currentSentence, word] }));
  },

  clearSentence: () => set({ currentSentence: [] }),
}));
