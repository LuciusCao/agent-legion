import { describe, it, expect } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { PhaseRunsPanel } from "./PhaseRunsPanel";
import type { TranscriptionRun } from "../types";

function makeRun(
  id: number,
  phase_key: string,
  status: string,
  opts: Partial<{
    started_at: string;
    finished_at: string | null;
    command_json: string;
    error_message: string;
  }> = {}
) {
  return {
    id,
    video_id: "v1",
    phase_key,
    status,
    started_at: opts.started_at ?? "2024-01-01T00:00:00Z",
    finished_at: opts.finished_at ?? null,
    command_json: opts.command_json ?? "[]",
    exit_code: status === "completed" ? 0 : null,
    log_path: "",
    error_message: opts.error_message ?? "",
  };
}

function makeTranscriptionRun(provider: string, status = "completed"): TranscriptionRun {
  return {
    id: 1,
    video_id: "v1",
    provider,
    status,
    started_at: "2024-01-01T00:00:00Z",
    finished_at: "2024-01-01T00:00:30Z",
    srt_entry_count: 12,
    validation_summary: "ok",
    fallback_reason: "",
  };
}

describe("PhaseRunsPanel", () => {
  it("renders latest view by default", () => {
    const runs = [
      makeRun(1, "download", "completed", { finished_at: "2024-01-01T00:00:30Z" }),
      makeRun(2, "transcribe", "running", { started_at: "2024-01-01T00:00:35Z" }),
    ];
    render(<PhaseRunsPanel phaseRuns={runs} transcriptionRuns={[]} contentType="knowledge" />);
    expect(screen.getByText("下载")).toBeInTheDocument();
    expect(screen.getByText("转录")).toBeInTheDocument();
    expect(screen.queryByText("字幕审核")).not.toBeInTheDocument();
  });

  it("switches to history view on button click", () => {
    const runs = [
      makeRun(1, "download", "completed", { finished_at: "2024-01-01T00:00:30Z" }),
      makeRun(2, "transcribe", "running", { started_at: "2024-01-01T00:00:35Z" }),
    ];
    render(<PhaseRunsPanel phaseRuns={runs} transcriptionRuns={[]} contentType="knowledge" />);
    fireEvent.click(screen.getByText("历史"));
    expect(screen.getByText("当前")).toBeInTheDocument();
    expect(screen.getByText("下载")).toBeInTheDocument();
    expect(screen.getByText("转录")).toBeInTheDocument();
  });

  it("shows occurrence count in history view for repeated phases", () => {
    const runs = [
      makeRun(1, "download", "completed", { finished_at: "2024-01-01T00:00:30Z" }),
      makeRun(2, "transcribe", "completed", { finished_at: "2024-01-01T00:01:00Z" }),
      makeRun(3, "transcribe", "failed", {
        started_at: "2024-01-01T00:05:00Z",
        finished_at: "2024-01-01T00:06:00Z",
        error_message: "fail",
      }),
    ];
    render(<PhaseRunsPanel phaseRuns={runs} transcriptionRuns={[]} contentType="knowledge" />);
    fireEvent.click(screen.getByText("历史"));
    expect(screen.getByText(/第2次/)).toBeInTheDocument();
  });

  it("shows correct summary after rerun from transcribe", () => {
    const runs = [
      makeRun(1, "download", "completed", { finished_at: "2024-01-01T00:00:30Z" }),
      makeRun(2, "transcribe", "running", { started_at: "2024-01-01T00:01:00Z" }),
    ];
    render(<PhaseRunsPanel phaseRuns={runs} transcriptionRuns={[]} contentType="knowledge" />);
    expect(screen.getByText(/1\s*\/\s*7/)).toBeInTheDocument();
    const badges = screen.getAllByText("处理中");
    expect(badges.length).toBeGreaterThanOrEqual(1);
  });

  it("does not include stale downstream phases in latest view after a middle-phase rerun", () => {
    const runs = [
      makeRun(1, "download", "completed", { finished_at: "2024-01-01T00:00:30Z" }),
      makeRun(2, "transcribe", "completed", { finished_at: "2024-01-01T00:01:30Z" }),
      makeRun(3, "subtitle_review", "completed", { finished_at: "2024-01-01T00:02:30Z" }),
      makeRun(4, "chapter_generate", "completed", { finished_at: "2024-01-01T00:03:30Z" }),
      makeRun(5, "interaction_generate", "completed", { finished_at: "2024-01-01T00:04:30Z" }),
      makeRun(6, "content_review", "completed", { finished_at: "2024-01-01T00:05:30Z" }),
      makeRun(7, "assemble", "completed", { finished_at: "2024-01-01T00:06:30Z" }),
      makeRun(8, "transcribe", "running", { started_at: "2024-01-01T00:10:00Z" }),
    ];

    render(<PhaseRunsPanel phaseRuns={runs} transcriptionRuns={[]} contentType="knowledge" />);

    expect(screen.getByText(/1\s*\/\s*7/)).toBeInTheDocument();
    expect(screen.getByText("下载")).toBeInTheDocument();
    expect(screen.getByText("转录")).toBeInTheDocument();
    expect(screen.queryByText("字幕审核")).not.toBeInTheDocument();
  });

  it("shows the rerun phase as queued before the worker creates a new run", () => {
    const runs = [
      makeRun(1, "download", "completed", { finished_at: "2024-01-01T00:00:30Z" }),
      makeRun(2, "transcribe", "completed", { finished_at: "2024-01-01T00:01:30Z" }),
      makeRun(3, "subtitle_review", "completed", { finished_at: "2024-01-01T00:02:30Z" }),
      makeRun(4, "chapter_generate", "completed", { finished_at: "2024-01-01T00:03:30Z" }),
      makeRun(5, "interaction_generate", "completed", { finished_at: "2024-01-01T00:04:30Z" }),
      makeRun(6, "content_review", "completed", { finished_at: "2024-01-01T00:05:30Z" }),
      makeRun(7, "assemble", "completed", { finished_at: "2024-01-01T00:06:30Z" }),
    ];

    render(
      <PhaseRunsPanel
        phaseRuns={runs}
        transcriptionRuns={[]}
        contentType="knowledge"
        currentPhase="transcribe"
        videoStatus="queued"
      />
    );

    expect(screen.getByText(/1\s*\/\s*7/)).toBeInTheDocument();
    expect(screen.getByText("下载")).toBeInTheDocument();
    expect(screen.getByText("转录")).toBeInTheDocument();
    expect(screen.getByText("排队中")).toBeInTheDocument();
    expect(screen.queryByText("字幕审核")).not.toBeInTheDocument();
  });

  it("shows empty state when no phase runs exist", () => {
    render(<PhaseRunsPanel phaseRuns={[]} transcriptionRuns={[]} contentType="knowledge" />);
    expect(screen.getByText("暂无处理记录")).toBeInTheDocument();
  });

  it("shows the transcription engine display name for transcribe runs", () => {
    const runs = [
      makeRun(1, "download", "completed", { finished_at: "2024-01-01T00:00:30Z" }),
      makeRun(2, "transcribe", "completed", { finished_at: "2024-01-01T00:01:00Z" }),
    ];

    render(
      <PhaseRunsPanel
        phaseRuns={runs}
        transcriptionRuns={[makeTranscriptionRun("whisper")]}
        contentType="knowledge"
      />
    );

    expect(screen.getByText("whisper.cpp")).toBeInTheDocument();
    expect(screen.queryByText("transcribe")).not.toBeInTheDocument();
  });

  it("shows SenseVoice for sensevoice transcription runs", () => {
    const runs = [
      makeRun(1, "download", "completed", { finished_at: "2024-01-01T00:00:30Z" }),
      makeRun(2, "transcribe", "completed", { finished_at: "2024-01-01T00:01:00Z" }),
    ];

    render(
      <PhaseRunsPanel
        phaseRuns={runs}
        transcriptionRuns={[makeTranscriptionRun("sensevoice")]}
        contentType="knowledge"
      />
    );

    expect(screen.getByText("SenseVoice")).toBeInTheDocument();
  });

  it("keeps a transcribe tool fallback when transcription run details are not loaded", () => {
    const runs = [
      makeRun(1, "download", "completed", { finished_at: "2024-01-01T00:00:30Z" }),
      makeRun(2, "transcribe", "running", { started_at: "2024-01-01T00:01:00Z" }),
    ];

    render(<PhaseRunsPanel phaseRuns={runs} transcriptionRuns={[]} contentType="knowledge" />);

    expect(screen.getByText("transcribe")).toBeInTheDocument();
  });

  it("shows the openclaw agent name for agent phase runs", () => {
    const runs = [
      makeRun(1, "download", "completed", { finished_at: "2024-01-01T00:00:30Z" }),
      makeRun(2, "transcribe", "completed", { finished_at: "2024-01-01T00:01:00Z" }),
      makeRun(3, "subtitle_review", "completed", {
        finished_at: "2024-01-01T00:02:00Z",
        command_json: JSON.stringify(["openclaw", "agent", "--agent", "agent_1"]),
      }),
    ];

    render(<PhaseRunsPanel phaseRuns={runs} transcriptionRuns={[]} contentType="knowledge" />);

    expect(screen.getByText("openclaw-agent_1")).toBeInTheDocument();
    expect(screen.queryByText("openclaw")).not.toBeInTheDocument();
  });
});
