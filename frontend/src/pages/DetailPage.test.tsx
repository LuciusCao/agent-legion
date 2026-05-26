import { describe, it, expect, vi, beforeEach } from "vitest";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { DetailPage } from "./DetailPage";
import { useDetailStore } from "../stores/detailStore";
import { useArtifactStore } from "../stores/artifactStore";
import { useInteractionStore } from "../stores/interactionStore";
import { useVideoStore } from "../stores/videoStore";
import { useUiStore } from "../stores/uiStore";

vi.mock("../api", () => ({
  api: vi.fn(),
}));

import { api } from "../api";

const mockApi = vi.mocked(api);

describe("DetailPage", () => {
  beforeEach(() => {
    global.ResizeObserver = vi.fn().mockImplementation(function () {
      return {
        observe: vi.fn(),
        disconnect: vi.fn(),
        unobserve: vi.fn(),
      };
    });
    useDetailStore.setState({
      currentVideo: null,
      log: "",
      activeTab: "subtitles",
      isLoading: false,
    });
    useArtifactStore.setState({
      artifacts: { subtitles: [], chapters: [], interactions: [], metadata: null, review: null, checklist: null },
    });
    useInteractionStore.setState({
      triggeredNodeIndexes: new Set(),
      dismissedNodeIndexes: new Set(),
      currentSentence: [],
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
      deleteDialogOpen: false,
      toast: null,
    });
    mockApi.mockReset();
  });

  it("caps the phase panel to the measured upper-left column height", async () => {
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
        phase_runs: [],
        transcription_runs: [],
      })
      .mockResolvedValueOnce({
        subtitles: [],
        chapters: [],
        interactions: [],
        metadata: null,
        review: null,
        checklist: null,
      })
      .mockResolvedValueOnce({ log: "ok" });

    const spy = vi
      .spyOn(Element.prototype, "getBoundingClientRect")
      .mockImplementation(function (this: Element) {
        if (this.classList.contains("detail-primary")) {
          return { height: 420 } as DOMRect;
        }
        return {
          height: 0,
          width: 0,
          top: 0,
          left: 0,
          bottom: 0,
          right: 0,
          x: 0,
          y: 0,
          toJSON: () => {},
        } as unknown as DOMRect;
      });

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

    const upper = document.querySelector(".detail-upper");
    const primary = document.querySelector(".detail-primary");
    const topbar = document.querySelector(".detail-topbar");
    const sidebar = document.querySelector(".phase-runs-sidebar") as HTMLElement;

    try {
      expect(upper).toContainElement(primary);
      expect(upper).toContainElement(sidebar);
      expect(primary).toContainElement(topbar);
      expect(sidebar.style.getPropertyValue("--detail-primary-height")).toBe("420px");
      expect(sidebar.style.height).toBe("");
    } finally {
      spy.mockRestore();
    }
  });

  it("renders timeline with chapters and interactions", async () => {
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
        phase_runs: [],
        transcription_runs: [],
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
      expect(screen.getByText("Video 1")).toBeInTheDocument();
    });

    // Timeline should show both chapters and interactions as chips
    // ChapterStrip + TimelineStrip both render md-suggestion-chip elements
    const chips = document.querySelectorAll("md-suggestion-chip");
    expect(chips.length).toBeGreaterThanOrEqual(2);

    // Subtitles should not be visible by default (in dialog only)
    expect(document.querySelector("md-dialog")).not.toBeInTheDocument();
  });

  it("opens interaction details from the more menu and replays a node", async () => {
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
        phase_runs: [],
        transcription_runs: [],
      })
      .mockResolvedValueOnce({
        subtitles: [],
        chapters: [],
        interactions: [{ id: "n1", trigger_time: 5, instruction: "节点内容", answer: ["hello"] }],
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

    fireEvent.click(screen.getByTitle("更多"));
    fireEvent.click(screen.getByText("交互节点"));

    const nodeEntry = await screen.findByText("节点内容");
    fireEvent.click(nodeEntry);

    expect(await screen.findByText("hello")).toBeInTheDocument();
  });

  it("exposes the full detail title through a custom hover tooltip", async () => {
    const longTitle = "x09050501 这是一个特别长的知识点名称，用来确认详情页标题悬停时显示全称";
    mockApi
      .mockResolvedValueOnce({
        video: {
          id: "v1",
          title: longTitle,
          source_url: "https://example.com/v1.mp4",
          content_type: "knowledge",
          external_id: "x09050501",
          knowledge_code: "x09050501",
          question_id: "",
          status: "completed",
          current_phase: "assemble",
          error_message: "",
          storage_dir: "/tmp/v1",
          duration: 120,
        },
        phase_runs: [],
        transcription_runs: [],
      })
      .mockResolvedValueOnce({
        subtitles: [],
        chapters: [],
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
      const heading = screen.getByRole("heading", { name: longTitle });
      expect(heading).not.toHaveAttribute("title");
      expect(heading.parentElement).toHaveAttribute("data-tooltip", longTitle);
    });
  });

  it("pauses and shows an interaction when playback crosses a trigger time", async () => {
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
        phase_runs: [],
        transcription_runs: [],
      })
      .mockResolvedValueOnce({
        subtitles: [],
        chapters: [],
        interactions: [
          { id: "n1", trigger_time: 5, type: "example_practice", instruction: "暂停做题" },
        ],
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
      expect(document.querySelector("video")).toBeInTheDocument();
    });
    const video = document.querySelector("video") as HTMLVideoElement;
    const pause = vi.fn();
    Object.defineProperty(video, "paused", { value: false, configurable: true });
    Object.defineProperty(video, "pause", { value: pause, configurable: true });
    Object.defineProperty(video, "currentTime", { value: 4.8, configurable: true });
    act(() => {
      video.dispatchEvent(new Event("timeupdate", { bubbles: true }));
    });

    Object.defineProperty(video, "currentTime", { value: 6.7, configurable: true });
    act(() => {
      video.dispatchEvent(new Event("timeupdate", { bubbles: true }));
    });

    expect(pause).toHaveBeenCalledTimes(1);
    expect(screen.getByText("暂停做题")).toBeInTheDocument();
  });

  it("keeps delete dialog open and shows an error when deleting fails", async () => {
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
        phase_runs: [],
        transcription_runs: [],
      })
      .mockResolvedValueOnce({
        subtitles: [],
        chapters: [],
        interactions: [],
        metadata: null,
        review: null,
        checklist: null,
      })
      .mockResolvedValueOnce({ log: "ok" })
      .mockRejectedValueOnce(new Error("delete failed"));

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
      useUiStore.getState().openDeleteDialog();
    });

    await act(async () => {
      screen.getByText("删除").click();
    });

    await waitFor(() => {
      expect(useUiStore.getState().deleteDialogOpen).toBe(true);
      expect(useUiStore.getState().toast).toEqual({
        message: "删除失败: delete failed",
        type: "error",
      });
    });
  });
});
