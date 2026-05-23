import { create } from "zustand";
import type { AgentStatus, ContentType } from "../types";

interface Toast {
  message: string;
  type: "success" | "error";
}

interface UiState {
  agents: AgentStatus[];
  addDialogOpen: boolean;
  addContentType: ContentType;
  rerunDialogOpen: boolean;
  toast: Toast | null;
  connectAgentsWs: () => void;
  openAddDialog: () => void;
  closeAddDialog: () => void;
  setAddContentType: (type: ContentType) => void;
  openRerunDialog: () => void;
  closeRerunDialog: () => void;
  showToast: (message: string, type: "success" | "error") => void;
  clearToast: () => void;
}

export const useUiStore = create<UiState>((set) => ({
  agents: [],
  addDialogOpen: false,
  addContentType: "knowledge",
  rerunDialogOpen: false,
  toast: null,

  connectAgentsWs: () => {
    const protocol = location.protocol === "https:" ? "wss:" : "ws:";
    const ws = new WebSocket(`${protocol}//${location.host}/api/agents`);
    ws.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data) as AgentStatus[];
        set({ agents: data });
      } catch {
        // ignore
      }
    };
    ws.onclose = () => {
      setTimeout(() => {
        set((state) => {
          state.connectAgentsWs();
          return {};
        });
      }, 3000);
    };
  },

  openAddDialog: () => set({ addDialogOpen: true, addContentType: "knowledge" }),
  closeAddDialog: () => set({ addDialogOpen: false }),
  setAddContentType: (type) => set({ addContentType: type }),
  openRerunDialog: () => set({ rerunDialogOpen: true }),
  closeRerunDialog: () => set({ rerunDialogOpen: false }),
  showToast: (message, type) => set({ toast: { message, type } }),
  clearToast: () => set({ toast: null }),
}));
