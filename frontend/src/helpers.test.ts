import { describe, expect, it } from "vitest";

import {
  canRerunFrom,
  computeProgress,
  escapeHtml,
  filterVideos,
  formatInteractionStats,
  getInteractionQuestion,
  getPhases,
  parseResourceInputs,
  parseResourceIds,
  statusGroup,
  visibleSelectedIds,
} from "./helpers";
import type { VideoItem } from "./types";

const video = (overrides: Partial<VideoItem>): VideoItem => ({
  id: "knowledge_K001",
  title: "奇函数求函数值",
  source_url: "https://example.com/k001.mp4",
  content_type: "knowledge",
  external_id: "K001",
  knowledge_code: "K001",
  question_id: "",
  source_uuid: "",
  status: "queued",
  current_phase: "download",
  error_message: "",
  ...overrides,
});

const makeVideo = (overrides: Partial<VideoItem> = {}): VideoItem => ({
  id: "v1",
  title: "Test",
  source_url: "",
  content_type: "knowledge",
  external_id: "",
  knowledge_code: "",
  question_id: "",
  source_uuid: "",
  status: "queued",
  current_phase: "download",
  error_message: "",
  ...overrides,
});

describe("statusGroup", () => {
  it("treats missing URLs as failed in the list status group", () => {
    expect(statusGroup(video({ status: "missing_url" }))).toBe("failed");
    expect(statusGroup(video({ status: "queued", current_phase: "waiting_for_url" }))).toBe(
      "failed",
    );
  });

  it("keeps terminal and running statuses distinct", () => {
    expect(statusGroup(video({ status: "failed" }))).toBe("failed");
    expect(statusGroup(video({ status: "completed" }))).toBe("completed");
    expect(statusGroup(video({ status: "running" }))).toBe("running");
  });
});

describe("filterVideos", () => {
  const videos = [
    video({ id: "knowledge_K001", external_id: "K001", title: "奇函数", status: "completed" }),
    video({
      id: "knowledge_K002",
      external_id: "K002",
      title: "未上架视频",
      status: "missing_url",
      current_phase: "waiting_for_url",
    }),
    video({
      id: "question_Q001",
      content_type: "question",
      external_id: "Q001",
      question_id: "Q001",
      knowledge_code: "",
      title: "题目解析",
      status: "failed",
    }),
  ];

  it("filters by resource type, status group, and search query", () => {
    expect(
      filterVideos(videos, {
        selectedType: "question",
        statusFilter: "failed",
        searchQuery: "q001",
      }).map((item) => item.id),
    ).toEqual(["question_Q001"]);
  });

  it("includes missing URLs when filtering failed resources", () => {
    expect(
      filterVideos(videos, {
        selectedType: "knowledge",
        statusFilter: "failed",
        searchQuery: "k002",
      }).map((item) => item.id),
    ).toEqual(["knowledge_K002"]);
  });
});

describe("visibleSelectedIds", () => {
  it("only returns selected ids that are still visible after filtering", () => {
    const visible = [video({ id: "knowledge_K001" })];
    const selected = new Set(["knowledge_K001", "knowledge_K002"]);

    expect(visibleSelectedIds(visible, selected)).toEqual(["knowledge_K001"]);
  });
});

describe("getInteractionQuestion", () => {
  it("supports nested question payloads", () => {
    const node = { question: { instruction: "暂停思考" }, instruction: "顶层" };

    expect(getInteractionQuestion(node)).toEqual({ instruction: "暂停思考" });
  });

  it("falls back to top-level interaction fields", () => {
    const node = { instruction: "暂停思考", hint: "提示" };

    expect(getInteractionQuestion(node)).toEqual(node);
  });
});

describe("escapeHtml", () => {
  it("escapes text before it is placed into HTML strings", () => {
    expect(escapeHtml(`<img src=x onerror="alert('x')">`)).toBe(
      "&lt;img src=x onerror=&quot;alert(&#039;x&#039;)&quot;&gt;",
    );
  });
});

describe("parseResourceIds", () => {
  it("splits ids by newlines and comma variants while trimming empty entries", () => {
    expect(parseResourceIds(" K001, K002\n\nQ001，Q002 ")).toEqual([
      "K001",
      "K002",
      "Q001",
      "Q002",
    ]);
  });
});

describe("parseResourceInputs", () => {
  it("keeps comma-separated batch ids as separate resources", () => {
    expect(parseResourceInputs(" K001, K002\n\nQ001，Q002 ")).toEqual([
      { external_id: "K001", source_uuid: "" },
      { external_id: "K002", source_uuid: "" },
      { external_id: "Q001", source_uuid: "" },
      { external_id: "Q002", source_uuid: "" },
    ]);
  });

  it("parses one external id and source uuid pair per line", () => {
    expect(parseResourceInputs("K001,uuid-1\nK002,uuid-2")).toEqual([
      { external_id: "K001", source_uuid: "uuid-1" },
      { external_id: "K002", source_uuid: "uuid-2" },
    ]);
  });

  it("parses full-width comma external id and source uuid pairs", () => {
    expect(parseResourceInputs("K001，uuid-1")).toEqual([
      { external_id: "K001", source_uuid: "uuid-1" },
    ]);
  });
});

describe("computeProgress", () => {
  it("returns 1 for completed videos", () => {
    expect(computeProgress(video({ status: "completed", current_phase: "assemble" }))).toBe(1);
  });

  it("returns 0 for waiting_for_url", () => {
    expect(computeProgress(video({ status: "missing_url", current_phase: "waiting_for_url" }))).toBe(0);
  });

  it("computes knowledge progress correctly", () => {
    // queued at download -> 0/7
    expect(computeProgress(video({ status: "queued", current_phase: "download" }))).toBe(0);
    // running at download -> 0.5/7
    expect(computeProgress(video({ status: "running", current_phase: "download" }))).toBe(0.5 / 7);
    // queued at transcribe -> 1/7
    expect(computeProgress(video({ status: "queued", current_phase: "transcribe" }))).toBe(1 / 7);
    // running at assemble -> 6.5/7
    expect(computeProgress(video({ status: "running", current_phase: "assemble" }))).toBe(6.5 / 7);
  });

  it("computes question progress correctly", () => {
    // queued at download -> 0/6
    expect(computeProgress(video({ content_type: "question", status: "queued", current_phase: "download" }))).toBe(0);
    // running at assemble -> 4.5/5
    expect(computeProgress(video({ content_type: "question", status: "running", current_phase: "assemble" }))).toBe(4.5 / 5);
    // completed
    expect(computeProgress(video({ content_type: "question", status: "completed", current_phase: "assemble" }))).toBe(1);
  });

  it("returns 0 for unknown phase", () => {
    expect(computeProgress(video({ status: "queued", current_phase: "unknown" }))).toBe(0);
  });
});

describe("getPhases", () => {
  it("returns knowledge phases for knowledge", () => {
    expect(getPhases("knowledge")).toEqual([
      "download",
      "transcribe",
      "subtitle_review",
      "chapter_generate",
      "interaction_generate",
      "content_review",
      "assemble",
    ]);
  });

  it("returns question phases for question", () => {
    expect(getPhases("question")).toEqual([
      "download",
      "transcribe",
      "subtitle_review",
      "chapter_generate",
      "assemble",
    ]);
  });
});

describe("canRerunFrom", () => {
  it("returns true for any phase when video is completed", () => {
    const completed = makeVideo({ status: "completed", current_phase: "assemble" });
    expect(canRerunFrom(completed, "download")).toBe(true);
    expect(canRerunFrom(completed, "assemble")).toBe(true);
  });

  it("returns true when selected phase is at or before current phase", () => {
    const v = makeVideo({ status: "running", current_phase: "chapter_generate" });
    expect(canRerunFrom(v, "download")).toBe(true);
    expect(canRerunFrom(v, "chapter_generate")).toBe(true);
    expect(canRerunFrom(v, "assemble")).toBe(false);
  });

  it("returns false for unknown current_phase", () => {
    const v = makeVideo({ status: "running", current_phase: "unknown_phase" });
    expect(canRerunFrom(v, "download")).toBe(false);
  });

  it("returns false for unknown phase argument", () => {
    const v = makeVideo({ status: "running", current_phase: "download" });
    expect(canRerunFrom(v, "unknown_phase")).toBe(false);
  });
});

describe("formatInteractionStats", () => {
  it("returns empty string for undefined stats", () => {
    expect(formatInteractionStats(undefined)).toBe("");
  });

  it("returns empty string for empty stats", () => {
    expect(formatInteractionStats({})).toBe("");
  });

  it("formats single type stats", () => {
    expect(formatInteractionStats({ example_practice: { passed: 2, total: 3 } })).toBe(
      "例题试做 2/3",
    );
  });

  it("formats multiple types in order", () => {
    expect(
      formatInteractionStats({
        example_practice: { passed: 2, total: 3 },
        interaction_summary: { passed: 1, total: 1 },
      }),
    ).toBe("例题试做 2/3 ｜ 互动小结 1/1");
  });

  it("puts unknown types after known types", () => {
    expect(
      formatInteractionStats({
        unknown_type: { passed: 1, total: 2 },
        example_practice: { passed: 2, total: 3 },
      }),
    ).toBe("例题试做 2/3 ｜ unknown_type 1/2");
  });

  it("treats video_summary same as interaction_summary in order", () => {
    expect(
      formatInteractionStats({
        video_summary: { passed: 1, total: 1 },
        example_practice: { passed: 2, total: 3 },
      }),
    ).toBe("例题试做 2/3 ｜ 互动小结 1/1");
  });
});
