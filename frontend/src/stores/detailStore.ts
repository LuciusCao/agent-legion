import { create } from "zustand";
import type { VideoItem, VideoArtifacts, DetailTab } from "../types";
import { api } from "../api";

interface DetailState {
  currentVideo: VideoItem | null;
  artifacts: VideoArtifacts;
  log: string;
  activeTab: DetailTab;
  triggeredNodeIndexes: Set<number>;
  currentSentence: string[];
  isLoading: boolean;
  loadVideo: (id: string) => Promise<void>;
  loadArtifacts: (id: string) => Promise<void>;
  loadLog: (id: string) => Promise<void>;
  setActiveTab: (tab: DetailTab) => void;
  triggerInteraction: (index: number) => void;
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
  activeTab: "nodes",
  triggeredNodeIndexes: new Set(),
  currentSentence: [],
  isLoading: false,

  loadVideo: async (id) => {
    set({ isLoading: true });
    try {
      const data = await api<{ videos: VideoItem[] }>("/api/videos");
      const video = data.videos.find((v) => v.id === id) || null;
      set({ currentVideo: video });
      if (video) {
        set({ activeTab: video.content_type === "question" ? "subtitles" : "nodes" });
      }
    } finally {
      set({ isLoading: false });
    }
  },

  loadArtifacts: async (id) => {
    try {
      const data = await api<any>(`/api/videos/${id}/artifacts`);
      set({
        artifacts: {
          subtitles: data.subtitles || [],
          chapters: (data.chapters || []).map((c: any) => ({
            id: c.id,
            start: c.start_time ?? c.start,
            end: c.end_time ?? c.end,
            title: c.title,
          })),
          interactions: data.interactions || [],
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

  setActiveTab: (tab) => set({ activeTab: tab }),

  triggerInteraction: (index) => {
    set((state) => {
      const next = new Set(state.triggeredNodeIndexes);
      next.add(index);
      return { triggeredNodeIndexes: next };
    });
  },

  resetSentence: () => set({ currentSentence: [] }),

  pushWord: (word) => {
    set((state) => ({ currentSentence: [...state.currentSentence, word] }));
  },

  clearSentence: () => set({ currentSentence: [] }),
}));
