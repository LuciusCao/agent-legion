import { create } from "zustand";
import type { VideoItem, ContentType } from "../types";
import { api } from "../api";

interface VideoState {
  videos: VideoItem[];
  selectedType: ContentType;
  statusFilter: string;
  searchQuery: string;
  selectMode: boolean;
  selectedIds: Set<string>;
  isLoading: boolean;
  fetchVideos: () => Promise<void>;
  setSelectedType: (type: ContentType) => void;
  setStatusFilter: (status: string) => void;
  setSearchQuery: (query: string) => void;
  toggleSelectMode: () => void;
  toggleVideoSelection: (id: string) => void;
  selectAllVisible: () => void;
  clearSelection: () => void;
  batchDelete: (ids: string[]) => Promise<{ results: Array<{ video_id: string; status: string; message?: string }> }>;
  batchRerun: (ids: string[], phase: string) => Promise<{ results: Array<{ video_id: string; status: string; message?: string }> }>;
  batchPackage: (ids: string[]) => Promise<{ path: string; download_url: string }>;
}

export const useVideoStore = create<VideoState>((set, get) => ({
  videos: [],
  selectedType: "knowledge",
  statusFilter: "all",
  searchQuery: "",
  selectMode: false,
  selectedIds: new Set(),
  isLoading: false,

  fetchVideos: async () => {
    set({ isLoading: true });
    try {
      const data = await api<{ videos: VideoItem[] }>("/api/videos");
      set({ videos: data.videos });
    } finally {
      set({ isLoading: false });
    }
  },

  setSelectedType: (type) => {
    set({ selectedType: type, selectedIds: new Set(), searchQuery: "" });
  },

  setStatusFilter: (status) => {
    set({ statusFilter: status, selectedIds: new Set() });
  },

  setSearchQuery: (query) => {
    set({ searchQuery: query, selectedIds: new Set() });
  },

  toggleSelectMode: () => {
    set((state) => ({ selectMode: !state.selectMode, selectedIds: new Set() }));
  },

  toggleVideoSelection: (id) => {
    set((state) => {
      const next = new Set(state.selectedIds);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return { selectedIds: next };
    });
  },

  selectAllVisible: () => {
    set((state) => {
      const filtered = state.videos.filter((v) => {
        if (state.selectedType && v.content_type !== state.selectedType) return false;
        if (state.statusFilter !== "all" && v.status !== state.statusFilter) return false;
        if (state.searchQuery) {
          const q = state.searchQuery.toLowerCase();
          const haystack = `${v.external_id} ${v.title} ${v.id}`.toLowerCase();
          if (!haystack.includes(q)) return false;
        }
        return true;
      });
      return { selectedIds: new Set(filtered.map((v) => v.id)) };
    });
  },

  clearSelection: () => set({ selectedIds: new Set() }),

  batchDelete: async (ids) => {
    return api("/api/videos/batch/delete", {
      method: "POST",
      body: JSON.stringify({ video_ids: ids }),
    });
  },

  batchRerun: async (ids, phase) => {
    return api("/api/videos/batch/rerun", {
      method: "POST",
      body: JSON.stringify({ video_ids: ids, phase }),
    });
  },

  batchPackage: async (ids) => {
    return api("/api/package", {
      method: "POST",
      body: JSON.stringify({ video_ids: ids }),
    });
  },
}));
