import { describe, it, expect, vi, beforeEach } from "vitest";
import { act, render, screen, waitFor } from "@testing-library/react";
import { BatchToolbar } from "./BatchToolbar";
import { useUiStore } from "../stores/uiStore";
import { useVideoStore } from "../stores/videoStore";

const mockApi = vi.fn();
vi.mock("../api", () => ({
  api: (...args: any[]) => mockApi(...args),
}));

describe("BatchToolbar", () => {
  beforeEach(() => {
    mockApi.mockReset();
    useUiStore.setState({
      agents: [],
      addDialogOpen: false,
      addContentType: "knowledge",
      rerunDialogOpen: false,
      deleteDialogOpen: false,
      toast: null,
    });
    useVideoStore.setState({
      videos: [
        {
          id: "v1",
          title: "Video 1",
          source_url: "",
          content_type: "knowledge",
          external_id: "K001",
          knowledge_code: "K001",
          question_id: "",
          status: "completed",
          current_phase: "package",
          error_message: "",
        },
        {
          id: "v2",
          title: "Video 2",
          source_url: "",
          content_type: "knowledge",
          external_id: "K002",
          knowledge_code: "K002",
          question_id: "",
          status: "completed",
          current_phase: "package",
          error_message: "",
        },
      ],
      selectedType: "knowledge",
      statusFilter: "all",
      searchQuery: "",
      selectMode: true,
      selectedIds: new Set(["v1", "v2"]),
      isLoading: false,
    });
    vi.spyOn(window, "confirm").mockReturnValue(true);
  });

  it("shows an error toast when batch delete has failed items", async () => {
    mockApi
      .mockResolvedValueOnce({
        results: [
          { video_id: "v1", status: "deleted", message: "" },
          { video_id: "v2", status: "not_found", message: "Video not found" },
        ],
      })
      .mockResolvedValueOnce({ videos: [] });

    render(<BatchToolbar />);

    await act(async () => {
      screen.getByTitle("删除").click();
    });

    await waitFor(() => {
      expect(useUiStore.getState().toast).toEqual({
        message: "删除完成：成功 1 项，失败 1 项",
        type: "error",
      });
    });
  });
});
