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
      packageSelectMode: false,
      selectedIds: new Set(),
      isLoading: false,
      error: null,
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

  it("toggles package select mode", () => {
    useVideoStore.getState().togglePackageSelectMode();
    expect(useVideoStore.getState().packageSelectMode).toBe(true);
    expect(useVideoStore.getState().selectMode).toBe(false);
    useVideoStore.getState().togglePackageSelectMode();
    expect(useVideoStore.getState().packageSelectMode).toBe(false);
  });

  it("selects all completed videos in package mode", () => {
    useVideoStore.setState({
      videos: [
        { id: "v1", title: "A", source_url: "", content_type: "knowledge", external_id: "K001", knowledge_code: "K001", question_id: "", source_uuid: "", status: "completed", current_phase: "package", error_message: "" },
        { id: "v2", title: "B", source_url: "", content_type: "knowledge", external_id: "K002", knowledge_code: "K002", question_id: "", source_uuid: "", status: "running", current_phase: "download", error_message: "" },
      ],
    });
    useVideoStore.getState().selectPackageAll();
    expect(useVideoStore.getState().selectedIds).toEqual(new Set(["v1"]));
  });

  it("selects only unpacked completed videos", () => {
    useVideoStore.setState({
      videos: [
        { id: "v1", title: "A", source_url: "", content_type: "knowledge", external_id: "K001", knowledge_code: "K001", question_id: "", source_uuid: "", status: "completed", current_phase: "package", error_message: "", packed: true },
        { id: "v2", title: "B", source_url: "", content_type: "knowledge", external_id: "K002", knowledge_code: "K002", question_id: "", source_uuid: "", status: "completed", current_phase: "package", error_message: "", packed: false },
      ],
    });
    useVideoStore.getState().selectPackageUnpacked();
    expect(useVideoStore.getState().selectedIds).toEqual(new Set(["v2"]));
  });

  it("toggles video selection", () => {
    useVideoStore.getState().toggleVideoSelection("v1");
    expect(useVideoStore.getState().selectedIds.has("v1")).toBe(true);
    useVideoStore.getState().toggleVideoSelection("v1");
    expect(useVideoStore.getState().selectedIds.has("v1")).toBe(false);
  });

  it("selects all videos visible under grouped status filters", () => {
    useVideoStore.setState({
      videos: [
        {
          id: "missing",
          title: "Missing URL",
          source_url: "",
          content_type: "knowledge",
          external_id: "K001",
          knowledge_code: "K001",
          question_id: "",
          source_uuid: "",
          status: "missing_url",
          current_phase: "waiting_for_url",
          error_message: "",
        },
        {
          id: "queued",
          title: "Queued",
          source_url: "",
          content_type: "knowledge",
          external_id: "K002",
          knowledge_code: "K002",
          question_id: "",
          source_uuid: "",
          status: "queued",
          current_phase: "download",
          error_message: "",
        },
      ],
      selectedType: "knowledge",
      statusFilter: "failed",
    });

    useVideoStore.getState().selectAllVisible();

    expect(useVideoStore.getState().selectedIds).toEqual(new Set(["missing"]));
  });
});
