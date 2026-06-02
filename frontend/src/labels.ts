import type { ContentType } from './types'

export const TYPE_LABELS: Record<ContentType, string> = {
  knowledge: '知识点',
  question: '题目',
}

export const STATUS_LABELS: Record<string, string> = {
  missing_url: '未获取到视频',
  queued: '排队中',
  running: '处理中',
  failed: '失败',
  completed: '已完成',
  pending: '待处理',
}

export const STATUS_ICONS: Record<string, string> = {
  completed: 'check_circle',
  running: 'sync',
  failed: 'error',
  queued: 'schedule',
  pending: 'radio_button_unchecked',
}

export const PHASE_LABELS: Record<string, string> = {
  __failed__: '失败的阶段',
  waiting_for_url: '未获取到视频',
  download: '下载',
  transcribe: '转录',
  subtitle_review: '字幕审核',
  chapter_generate: '章节生成',
  interaction_generate: '互动生成',
  content_review: '内容审核',
  assemble: '组装',
  package: '打包',
}

export const KNOWLEDGE_PHASES = [
  'download',
  'transcribe',
  'subtitle_review',
  'chapter_generate',
  'interaction_generate',
  'content_review',
  'assemble',
]

export const QUESTION_PHASES = [
  'download',
  'transcribe',
  'subtitle_review',
  'chapter_generate',
  'assemble',
]

export const INTERACTION_TYPE_LABELS: Record<string, string> = {
  example_practice: '例题试做',
  interaction_summary: '互动小结',
  video_summary: '互动小结',
}

export const INTERACTION_REVIEW_STATUS_LABELS: Record<string, string> = {
  all_passed: '全通过',
  partial: '部分通过',
  all_failed: '失败',
}
