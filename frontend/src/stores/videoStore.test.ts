import { describe, it, expect, vi, beforeEach } from "vitest";
import { useVideoStore } from "./videoStore";

vi.mock("../api", () => ({
  api: vi.fn(),
}));

import { api } from "../api";

const mockApi = vi.mocked(api);

describe("videoStore", () => {
  beforeEach(() => {
    useVideoStore.setState({
      videos: [],
      selectedType: "knowledge",
      statusFilter: "all",
      searchQuery: "",
      selectMode: false,
      selectedIds: new Set(),
      isLoading: false,
    });
    mockApi.mockClear();
  });

  it("fetches videos", async () => {
    mockApi.mockResolvedValueOnce({ videos: [{ id: "v1", title: "Test", content_type: "knowledge", status: "queued" }] });
    await useVideoStore.getState().fetchVideos();
    expect(useVideoStore.getState().videos).toHaveLength(1);
    expect(useVideoStore.getState().videos[0].id).toBe("v1");
  });

  it("toggles select mode", () => {
    useVideoStore.getState().toggleSelectMode();
    expect(useVideoStore.getState().selectMode).toBe(true);
  });

  it("toggles video selection", () => {
    useVideoStore.getState().toggleVideoSelection("v1");
    expect(useVideoStore.getState().selectedIds.has("v1")).toBe(true);
    useVideoStore.getState().toggleVideoSelection("v1");
    expect(useVideoStore.getState().selectedIds.has("v1")).toBe(false);
  });
});
