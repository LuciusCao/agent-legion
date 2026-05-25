import { describe, it, expect, vi, beforeEach } from "vitest";
import { useArtifactStore } from "./artifactStore";

vi.mock("../api", () => ({
  api: vi.fn(),
}));

import { api } from "../api";
const mockApi = vi.mocked(api);

describe("artifactStore", () => {
  beforeEach(() => {
    useArtifactStore.setState({
      artifacts: { subtitles: [], chapters: [], interactions: [], metadata: null, review: null, checklist: null },
    });
    mockApi.mockClear();
  });

  it("loads artifacts from api", async () => {
    mockApi.mockResolvedValueOnce({
      subtitles: [{ index: 1, start: 1, end: 3, text: "hello" }],
      chapters: [{ id: "c1", start_time: 10, end_time: 20, title: "Ch1" }],
      interactions: [{ trigger_time: 5, instruction: "test" }],
      metadata: { title: "Test" },
      review: { score: 90 },
      checklist: { items: [] },
    });

    await useArtifactStore.getState().loadArtifacts("v1");

    const state = useArtifactStore.getState().artifacts;
    expect(state.subtitles).toHaveLength(1);
    expect(state.chapters[0].start).toBe(10);
    expect(state.interactions).toHaveLength(1);
    expect(state.metadata).toEqual({ title: "Test" });
    expect(state.review).toEqual({ score: 90 });
    expect(state.checklist).toEqual({ items: [] });
  });

  it("falls back to empty artifacts on error", async () => {
    mockApi.mockRejectedValueOnce(new Error("fail"));
    await useArtifactStore.getState().loadArtifacts("v1");
    expect(useArtifactStore.getState().artifacts.subtitles).toHaveLength(0);
  });

  it("resets artifacts", () => {
    useArtifactStore.setState({
      artifacts: {
        subtitles: [{ index: 1, start: 1, end: 3, text: "hello" }],
        chapters: [],
        interactions: [],
        metadata: null,
        review: null,
        checklist: null,
      },
    });
    useArtifactStore.getState().resetArtifacts();
    expect(useArtifactStore.getState().artifacts.subtitles).toHaveLength(0);
  });
});
