export type ContentType = "knowledge" | "question";
export type ViewName = "list" | "detail";
export type DetailTab = "nodes" | "subtitles" | "chapters" | "logs" | "metadata" | "review";

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
  storage_dir?: string;
  duration?: number;
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

export type Chapter = {
  id?: string;
  start: number;
  end?: number;
  title: string;
};

export type InteractionOption = {
  id: string;
  text: string;
  is_distractor: boolean;
};

export type InteractionNode = {
  id?: string;
  type?: string;
  trigger_time?: number;
  instruction?: string;
  hint?: string;
  reference_sentence?: string;
  options?: InteractionOption[];
  answer?: string[];
  grading_mode?: string;
};

export type VideoArtifacts = {
  subtitles: Array<{ index: number; start: number; end: number; text: string }>;
  chapters: Chapter[];
  interactions: InteractionNode[];
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
