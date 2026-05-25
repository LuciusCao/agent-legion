import type { ContentType, VideoItem } from "./types";

export type VideoFilter = {
  selectedType: ContentType;
  statusFilter: string;
  searchQuery: string;
};

export function statusGroup(video: VideoItem): string {
  if (video.status === "missing_url" || video.current_phase === "waiting_for_url") return "failed";
  if (video.status === "failed") return "failed";
  if (video.status === "completed") return "completed";
  if (video.status === "running") return "running";
  return "queued";
}

export function filterVideos(videos: VideoItem[], filter: VideoFilter): VideoItem[] {
  const query = filter.searchQuery.trim().toLowerCase();
  return videos.filter((video) => {
    if (video.content_type !== filter.selectedType) return false;
    if (filter.statusFilter !== "all" && statusGroup(video) !== filter.statusFilter) return false;
    if (!query) return true;
    return [video.id, video.title, video.external_id].some((value) =>
      value.toLowerCase().includes(query),
    );
  });
}

export function visibleSelectedIds(visibleVideos: VideoItem[], selectedIds: Set<string>): string[] {
  const visibleIds = new Set(visibleVideos.map((video) => video.id));
  return Array.from(selectedIds).filter((id) => visibleIds.has(id));
}

export function getInteractionQuestion(node: Record<string, unknown>): Record<string, unknown> {
  return node.question && typeof node.question === "object"
    ? (node.question as Record<string, unknown>)
    : node;
}

export function escapeHtml(value: string): string {
  return value.replace(/[&<>"']/g, (char) => {
    const map: Record<string, string> = {
      "&": "&amp;",
      "<": "&lt;",
      ">": "&gt;",
      '"': "&quot;",
      "'": "&#039;",
    };
    return map[char] ?? char;
  });
}

export function parseResourceIds(value: string): string[] {
  return value
    .split(/[\n,，]/)
    .map((item) => item.trim())
    .filter(Boolean);
}

function looksLikeSourceUuid(value: string): boolean {
  return (
    /^uuid[-_]/i.test(value) ||
    /^[0-9a-f]{32}$/i.test(value) ||
    /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i.test(value)
  );
}

export function parseResourceInputs(value: string): { external_id: string; source_uuid: string }[] {
  const items: { external_id: string; source_uuid: string }[] = [];
  value
    .split("\n")
    .map((line) => line.trim())
    .filter(Boolean)
    .forEach((line) => {
      const pair = line.split(/[,，]/).map((part) => part.trim()).filter(Boolean);
      if (pair.length === 2 && looksLikeSourceUuid(pair[1])) {
        items.push({ external_id: pair[0], source_uuid: pair[1] });
        return;
      }
      line
        .split(/[,，]/)
        .map((part) => part.trim())
        .filter(Boolean)
        .forEach((externalId) => items.push({ external_id: externalId, source_uuid: "" }));
    });
  return items;
}

export function seconds(value: number): string {
  const minutes = Math.floor(value / 60);
  const secs = Math.floor(value % 60);
  return `${minutes}:${secs.toString().padStart(2, "0")}`;
}

const KNOWLEDGE_PHASE_SEQUENCE = [
  "download",
  "transcribe",
  "subtitle_review",
  "chapter_generate",
  "interaction_generate",
  "content_review",
  "assemble",
  "package",
];

const QUESTION_PHASE_SEQUENCE = [
  "download",
  "transcribe",
  "subtitle_review",
  "chapter_generate",
  "assemble",
  "package",
];

export function computeProgress(video: VideoItem): number {
  if (video.status === "completed") return 1;
  if (video.current_phase === "waiting_for_url") return 0;

  const phases =
    video.content_type === "question"
      ? QUESTION_PHASE_SEQUENCE
      : KNOWLEDGE_PHASE_SEQUENCE;
  const index = phases.indexOf(video.current_phase);
  if (index === -1) return 0;

  if (video.status === "running") {
    return (index + 0.5) / phases.length;
  }
  return index / phases.length;
}

export function getPhases(contentType: ContentType): string[] {
  return contentType === "question" ? QUESTION_PHASE_SEQUENCE : KNOWLEDGE_PHASE_SEQUENCE;
}

export function canRerunFrom(video: VideoItem, phase: string): boolean {
  if (video.status === "completed") return true;
  const phases = getPhases(video.content_type);
  const currentIdx = phases.indexOf(video.current_phase);
  const phaseIdx = phases.indexOf(phase);
  if (currentIdx === -1 || phaseIdx === -1) return false;
  return phaseIdx <= currentIdx;
}
