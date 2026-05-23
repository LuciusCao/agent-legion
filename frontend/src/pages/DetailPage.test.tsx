import { describe, it, expect, vi, beforeEach } from "vitest";
import { act, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { DetailPage } from "./DetailPage";
import { useDetailStore } from "../stores/detailStore";
import { useVideoStore } from "../stores/videoStore";
import { useUiStore } from "../stores/uiStore";

vi.mock("../api", () => ({
  api: vi.fn(),
}));

import { api } from "../api";

const mockApi = vi.mocked(api);

describe("DetailPage", () => {
  beforeEach(() => {
    useDetailStore.setState({
      currentVideo: null,
      artifacts: { subtitles: [], chapters: [], interactions: [], metadata: null, review: null, checklist: null },
      log: "",
      activeTab: "chapters",
      triggeredNodeIndexes: new Set(),
      currentSentence: [],
      isLoading: false,
    });
    useVideoStore.setState({
      videos: [],
      selectedType: "knowledge",
      statusFilter: "all",
      searchQuery: "",
      selectMode: false,
      selectedIds: new Set(),
      isLoading: false,
    });
    useUiStore.setState({
      agents: [],
      addDialogOpen: false,
      addContentType: "knowledge",
      rerunDialogOpen: false,
      toast: null,
    });
    mockApi.mockReset();
  });

  it("renders chapter content when chapters tab is active", async () => {
    mockApi
      .mockResolvedValueOnce({
        video: {
          id: "v1",
          title: "Video 1",
          source_url: "https://example.com/v1.mp4",
          content_type: "knowledge",
          external_id: "K001",
          knowledge_code: "K001",
          question_id: "",
          status: "completed",
          current_phase: "assemble",
          error_message: "",
          storage_dir: "/tmp/v1",
          duration: 120,
        },
      })
      .mockResolvedValueOnce({
        subtitles: [],
        chapters: [{ id: "c1", start: 12, end: 30, title: "第一章" }],
        interactions: [],
        metadata: null,
        review: null,
        checklist: null,
      })
      .mockResolvedValueOnce({ log: "ok" });

    render(
      <MemoryRouter initialEntries={["/videos/v1"]}>
        <Routes>
          <Route path="/videos/:id" element={<DetailPage />} />
        </Routes>
      </MemoryRouter>
    );

    await waitFor(() => {
      expect(screen.getByText("Video 1")).toBeInTheDocument();
    });
    act(() => {
      useDetailStore.getState().setActiveTab("chapters");
    });

    await waitFor(() => {
      expect(screen.getByText("第一章")).toBeInTheDocument();
    });
    expect(screen.getByText("0:12")).toBeInTheDocument();
  });

  it("switches tab panel when a material tab change event fires", async () => {
    mockApi
      .mockResolvedValueOnce({
        video: {
          id: "v1",
          title: "Video 1",
          source_url: "https://example.com/v1.mp4",
          content_type: "knowledge",
          external_id: "K001",
          knowledge_code: "K001",
          question_id: "",
          status: "completed",
          current_phase: "assemble",
          error_message: "",
          storage_dir: "/tmp/v1",
          duration: 120,
        },
      })
      .mockResolvedValueOnce({
        subtitles: [{ index: 1, start: 1, end: 3, text: "字幕内容" }],
        chapters: [{ id: "c1", start: 12, end: 30, title: "第一章" }],
        interactions: [{ trigger_time: 5, instruction: "节点内容" }],
        metadata: null,
        review: null,
        checklist: null,
      })
      .mockResolvedValueOnce({ log: "ok" });

    render(
      <MemoryRouter initialEntries={["/videos/v1"]}>
        <Routes>
          <Route path="/videos/:id" element={<DetailPage />} />
        </Routes>
      </MemoryRouter>
    );

    await waitFor(() => {
      expect(screen.getByText("节点内容")).toBeInTheDocument();
    });

    const tabs = document.querySelector("md-tabs") as HTMLElement & { activeTabIndex: number };
    act(() => {
      tabs.activeTabIndex = 2;
      tabs.dispatchEvent(new Event("change", { bubbles: true }));
    });

    await waitFor(() => {
      expect(screen.getByText("第一章")).toBeInTheDocument();
    });
    expect(screen.queryByText("节点内容")).not.toBeInTheDocument();
  });
});
