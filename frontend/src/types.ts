export type ContentType = "knowledge" | "question";
export type ViewName = "list" | "detail";
export type DetailTab = "nodes" | "subtitles" | "chapters" | "logs" | "metadata";

export type VideoItem = {
  id: string;
  title: string;
  source_url: string;
  content_type: ContentType;
  external_id: string;
  knowledge_code: string;
  question_id: string;
  status: string;
  current_phase: string;
  error_message: string;
};

export type AgentStatus = {
  id: string;
  name: string;
  busy: boolean;
  current_video_id: string | null;
  current_title?: string;
  current_content_type?: ContentType | "";
  current_external_id?: string;
  current_phase?: string;
};

export type VideoArtifacts = {
  subtitles: Array<{ index: number; start: number; end: number; text: string }>;
  chapters: Array<{ id: string; start_time: number; end_time: number; title: string }>;
  interactions: Array<Record<string, unknown>>;
  metadata: Record<string, unknown> | null;
  review: Record<string, unknown> | null;
  checklist: Record<string, unknown> | null;
};

export type AddResult = {
  external_id: string;
  content_type: ContentType;
  status: string;
  message: string;
  video?: VideoItem;
};

export type BatchResult = {
  video_id: string;
  status: string;
  phase?: string;
  message: string;
};
