import { useState, useCallback } from "react";
import { useUiStore } from "../stores/uiStore";
import { PHASE_LABELS } from "../labels";
import type { VideoItem } from "../types";

interface RerunDialogProps {
  video: VideoItem | null;
  onConfirm: (phase: string) => void;
}

const PHASES = [
  "download",
  "transcribe",
  "subtitle_review",
  "chapter_generate",
  "interaction_generate",
  "content_review",
  "assemble",
  "package",
];

export function RerunDialog({ video, onConfirm }: RerunDialogProps) {
  const { rerunDialogOpen, closeRerunDialog } = useUiStore();
  const [selectedPhase, setSelectedPhase] = useState("download");

  const handleConfirm = useCallback(() => {
    onConfirm(selectedPhase);
    closeRerunDialog();
  }, [selectedPhase, onConfirm, closeRerunDialog]);

  const availablePhases = video
    ? video.content_type === "question"
      ? PHASES.filter((p) => !["interaction_generate", "content_review"].includes(p))
      : PHASES
    : PHASES;

  return (
    <md-dialog open={rerunDialogOpen} onClosed={closeRerunDialog}>
      <div slot="headline">选择重跑阶段</div>
      <form slot="content" method="dialog">
        <md-list>
          {availablePhases.map((phase) => (
            <md-list-item key={phase} type="button" onClick={() => setSelectedPhase(phase)}>
              <md-radio slot="start" checked={selectedPhase === phase} />
              <div slot="headline">{PHASE_LABELS[phase] || phase}</div>
            </md-list-item>
          ))}
        </md-list>
      </form>
      <div slot="actions">
        <md-text-button onClick={closeRerunDialog}>取消</md-text-button>
        <md-filled-button onClick={handleConfirm}>确认</md-filled-button>
      </div>
    </md-dialog>
  );
}
