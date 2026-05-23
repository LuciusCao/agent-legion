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

export type InteractionNode = {
  trigger_time?: number | string;
  node_type?: string;
  type?: string;
  content?: {
    question?: string;
    options?: string[];
    word_bank?: string[];
  };
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
