import { PHASE_LABELS } from "../labels";
import type { VideoItem } from "../types";

const KNOWLEDGE_PHASES = [
  "download",
  "transcribe",
  "subtitle_review",
  "chapter_generate",
  "interaction_generate",
  "content_review",
  "assemble",
  "package",
];

const QUESTION_PHASES = [
  "download",
  "transcribe",
  "subtitle_review",
  "chapter_generate",
  "assemble",
  "package",
];

type StepState = "completed" | "running" | "failed" | "pending";

function getStepState(video: VideoItem, phaseIndex: number, currentIndex: number): StepState {
  if (video.status === "completed") return "completed";
  if (video.current_phase === "waiting_for_url") return "pending";

  if (phaseIndex < currentIndex) return "completed";
  if (phaseIndex > currentIndex) return "pending";

  // phaseIndex === currentIndex
  if (video.status === "failed") return "failed";
  if (video.status === "running") return "running";
  return "pending";
}

export function PhaseStepper({ video }: { video: VideoItem }) {
  const phases = video.content_type === "question" ? QUESTION_PHASES : KNOWLEDGE_PHASES;
  const currentIndex = phases.indexOf(video.current_phase);

  return (
    <div className="phase-stepper">
      {phases.map((phase, index) => {
        const state = getStepState(video, index, currentIndex);
        return (
          <div key={phase} className="step" title={PHASE_LABELS[phase] || phase}>
            <div className={`step-bar ${state}`} />
          </div>
        );
      })}
    </div>
  );
}
