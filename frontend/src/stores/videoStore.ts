import { create } from "zustand";
import type { VideoItem, ContentType } from "../types";
import { api } from "../api";
import { useUiStore } from "./uiStore";
import { filterVideos } from "../helpers";

interface VideoState {
  videos: VideoItem[];
  selectedType: ContentType;
  statusFilter: string;
  searchQuery: string;
  selectMode: boolean;
  packageSelectMode: boolean;
  selectedIds: Set<string>;
  isLoading: boolean;
  sseConnected: boolean;
  fetchVideos: () => Promise<void>;
  mergeVideo: (video: VideoItem) => void;
  removeVideo: (videoId: string) => void;
  setSelectedType: (type: ContentType) => void;
  setStatusFilter: (status: string) => void;
  setSearchQuery: (query: string) => void;
  toggleSelectMode: () => void;
  togglePackageSelectMode: () => void;
  toggleVideoSelection: (id: string) => void;
  selectAllVisible: () => void;
  selectPackageAll: () => void;
  selectPackageUnpacked: () => void;
  clearSelection: () => void;
  setSseConnected: (connected: boolean) => void;
  batchDelete: (ids: string[]) => Promise<{ results: Array<{ video_id: string; status: string; message?: string }> }>;
  batchRerun: (ids: string[], phase: string) => Promise<{ results: Array<{ video_id: string; status: string; message?: string }> }>;
  batchPackage: (ids: string[]) => Promise<{ path: string; download_url: string }>;
}

export const useVideoStore = create<VideoState>((set, _get) => ({
  videos: [],
  selectedType: "knowledge",
  statusFilter: "all",
  searchQuery: "",
  selectMode: false,
  packageSelectMode: false,
  selectedIds: new Set(),
  isLoading: false,
  sseConnected: true,

  fetchVideos: async () => {
    set({ isLoading: true });
    try {
      const data = await api<{ videos: VideoItem[] }>("/api/videos");
      set({ videos: data.videos });
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      useUiStore.getState().showToast(`加载失败: ${message}`, "error");
    } finally {
      set({ isLoading: false });
    }
  },

  mergeVideo: (video) => {
    set((state) => {
      const index = state.videos.findIndex((v) => v.id === video.id);
      if (index >= 0) {
        const next = [...state.videos];
        next[index] = video;
        return { videos: next };
      }
      return { videos: [video, ...state.videos] };
    });
  },

  removeVideo: (videoId) => {
    set((state) => ({
      videos: state.videos.filter((v) => v.id !== videoId),
      selectedIds: new Set([...state.selectedIds].filter((id) => id !== videoId)),
    }));
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
    set((state) => ({ selectMode: !state.selectMode, packageSelectMode: false, selectedIds: new Set() }));
  },

  togglePackageSelectMode: () => set((state) => ({
    packageSelectMode: !state.packageSelectMode,
    selectMode: false,
    selectedIds: new Set(),
  })),

  selectPackageAll: () => set((state) => {
    const completed = state.videos.filter((v) => v.status === "completed");
    return { selectedIds: new Set(completed.map((v) => v.id)) };
  }),

  selectPackageUnpacked: () => set((state) => {
    const unpacked = state.videos.filter((v) => v.status === "completed" && !v.packed);
    return { selectedIds: new Set(unpacked.map((v) => v.id)) };
  }),

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
      const filtered = filterVideos(state.videos, {
        selectedType: state.selectedType,
        statusFilter: state.statusFilter,
        searchQuery: state.searchQuery,
      });
      return { selectedIds: new Set(filtered.map((v) => v.id)) };
    });
  },

  clearSelection: () => set({ selectedIds: new Set() }),
  setSseConnected: (connected) => set({ sseConnected: connected }),

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
    const result = await api("/api/package", {
      method: "POST",
      body: JSON.stringify({ video_ids: ids }),
    });
    await get().fetchVideos();
    return result;
  },
}));
