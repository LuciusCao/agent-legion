import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { VideoPlayer } from "./VideoPlayer";

describe("VideoPlayer", () => {
  it("renders empty state when no video URL", () => {
    render(
      <VideoPlayer
        video={{ id: "v1", title: "Test", content_type: "knowledge", status: "queued" } as any}
        artifacts={{ subtitles: [], chapters: [], interactions: [], metadata: null, review: null, checklist: null }}
        onTimeUpdate={vi.fn()}
      />
    );
    expect(screen.getByText("视频文件未下载")).toBeInTheDocument();
  });
});
