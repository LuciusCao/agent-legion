import { useState, useCallback, useEffect, useRef } from "react";
import { useUiStore } from "../stores/uiStore";
import { KNOWLEDGE_PHASES, QUESTION_PHASES, PHASE_LABELS } from "../labels";
import type { VideoItem } from "../types";

interface RerunDialogProps {
  video: VideoItem | null;
  onConfirm: (phase: string) => void;
}

function getPhaseSequence(contentType: string): string[] {
  return contentType === "question" ? QUESTION_PHASES : KNOWLEDGE_PHASES;
}

function getAvailablePhases(video: VideoItem | null): string[] {
  if (!video) return KNOWLEDGE_PHASES;
  const sequence = getPhaseSequence(video.content_type);
  if (video.status === "completed") {
    return sequence;
  }
  const currentIndex = sequence.indexOf(video.current_phase);
  if (currentIndex === -1) {
    return sequence;
  }
  return sequence.slice(0, currentIndex + 1);
}

export function RerunDialog({ video, onConfirm }: RerunDialogProps) {
  const { rerunDialogOpen, closeRerunDialog } = useUiStore();
  const availablePhases = video ? getAvailablePhases(video) : KNOWLEDGE_PHASES;
  const [selectedPhase, setSelectedPhase] = useState(availablePhases[0] || "download");
  const radioRefs = useRef<Map<string, HTMLElement>>(new Map());

  const handleConfirm = useCallback(() => {
    onConfirm(selectedPhase);
    closeRerunDialog();
  }, [selectedPhase, onConfirm, closeRerunDialog]);

  useEffect(() => {
    if (!rerunDialogOpen) return;
    availablePhases.forEach((phase) => {
      const el = radioRefs.current.get(phase);
      if (el) {
        (el as any).checked = phase === selectedPhase;
      }
    });
  }, [rerunDialogOpen, selectedPhase, availablePhases]);

  if (!rerunDialogOpen) return null;

  return (
    <md-dialog open onClosed={closeRerunDialog}>
      <div slot="headline">选择重跑阶段</div>
      <form slot="content" method="dialog">
        <md-list>
          {availablePhases.map((phase) => (
            <md-list-item key={phase} type="button" onClick={() => setSelectedPhase(phase)}>
              <md-radio
                slot="start"
                name="rerun-phase"
                ref={(el: HTMLElement | null) => {
                  if (el) {
                    radioRefs.current.set(phase, el);
                  } else {
                    radioRefs.current.delete(phase);
                  }
                }}
              />
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
