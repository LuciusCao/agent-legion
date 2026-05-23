import type { ContentType } from "./types";

export const TYPE_LABELS: Record<ContentType, string> = { knowledge: "知识点", question: "题目" };

export const STATUS_LABELS: Record<string, string> = {
  missing_url: "未获取到视频",
  queued: "排队中",
  running: "处理中",
  failed: "失败",
  completed: "已完成",
};

export const PHASE_LABELS: Record<string, string> = {
  waiting_for_url: "未获取到视频",
  download: "下载",
  transcribe: "转录",
  subtitle_review: "字幕 review",
  chapter_generate: "章节生成",
  interaction_generate: "互动生成",
  content_review: "内容 review",
  assemble: "组装",
  package: "打包",
};

export const KNOWLEDGE_PHASES = [
  "download",
  "transcribe",
  "subtitle_review",
  "chapter_generate",
  "interaction_generate",
  "content_review",
  "assemble",
];

export const QUESTION_PHASES = ["download", "transcribe", "subtitle_review", "chapter_generate", "assemble"];
