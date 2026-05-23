import { describe, it, expect, vi, beforeEach } from "vitest";
import { useDetailStore } from "./detailStore";

vi.mock("../api", () => ({
  api: vi.fn(),
}));

import { api } from "../api";
const mockApi = vi.mocked(api);

describe("detailStore", () => {
  beforeEach(() => {
    useDetailStore.setState({
      currentVideo: null,
      artifacts: { subtitles: [], chapters: [], interactions: [], metadata: null, review: null, checklist: null },
      log: "",
      activeTab: "nodes",
      triggeredNodeIndexes: new Set(),
      currentSentence: [],
      isLoading: false,
    });
    mockApi.mockClear();
  });

  it("loads video and sets active tab", async () => {
    mockApi.mockResolvedValueOnce({
      video: { id: "v1", title: "Test", content_type: "question", status: "queued" },
    });
    await useDetailStore.getState().loadVideo("v1");
    expect(useDetailStore.getState().currentVideo?.id).toBe("v1");
    expect(useDetailStore.getState().activeTab).toBe("subtitles");
  });

  it("triggers interaction", () => {
    useDetailStore.getState().triggerInteraction(0);
    expect(useDetailStore.getState().triggeredNodeIndexes.has(0)).toBe(true);
  });

  it("resets interaction state when loading a different video", async () => {
    useDetailStore.setState({
      triggeredNodeIndexes: new Set([0, 2]),
      currentSentence: ["old", "state"],
    });
    mockApi.mockResolvedValueOnce({
      video: { id: "v2", title: "Fresh", content_type: "knowledge", status: "queued" },
    });

    await useDetailStore.getState().loadVideo("v2");

    expect(useDetailStore.getState().triggeredNodeIndexes.size).toBe(0);
    expect(useDetailStore.getState().currentSentence).toEqual([]);
  });
});
