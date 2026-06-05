export type ContentType = 'knowledge' | 'question'
export type DetailTab = 'nodes' | 'subtitles' | 'logs' | 'metadata'
export type RunToMode = 'continue' | 'rerun'

export type InteractionStats = {
  passed: number
  total: number
}

export type VideoItem = {
  id: string
  title: string
  source_url: string
  content_type: ContentType
  external_id: string
  knowledge_code: string
  question_id: string
  source_uuid: string
  status: string
  current_phase: string
  error_message: string
  storage_dir: string
  duration: number
  packed: boolean
  interaction_stats?: Record<string, InteractionStats>
  interaction_review_status?: 'all_passed' | 'partial' | 'all_failed'
}

export type AgentStatus = {
  id: string
  name: string
  busy: boolean
  task_count: number
  max_tasks: number
  current_video_id: string | null
  current_title?: string
  current_content_type?: ContentType | ''
  current_external_id?: string
  current_phase?: string
}

export type Chapter = {
  id?: string
  start: number
  end?: number
  title: string
}

export type InteractionOption = {
  id: string
  text: string
  is_distractor: boolean
}

export type InteractionNode = {
  id?: string
  type?: string
  trigger_time?: number | string
  instruction?: string
  hint?: string
  reference_sentence?: string
  options?: InteractionOption[]
  answer?: string[]
  grading_mode?: string
}

export type VideoArtifacts = {
  subtitles: Array<{ index: number; start: number; end: number; text: string }>
  chapters: Chapter[]
  interactions: InteractionNode[]
  metadata: Record<string, unknown> | null
  review: Record<string, unknown> | null
  checklist: Record<string, unknown> | null
}

export type AddResult = {
  external_id: string
  content_type: ContentType
  status: string
  message: string
  video?: VideoItem
}

export type BatchResult = {
  video_id: string
  status: string
  phase?: string
  message: string
}

export type RunToResult = {
  video_id: string
  status: string
  phase: string
  message: string
}

export type PhaseRun = {
  id: number
  video_id: string
  phase_key: string
  status: string
  started_at: string
  finished_at: string | null
  command_json: string
  exit_code: number | null
  log_path: string
  error_message: string
  agent_id?: string
  agent_session_id?: string
}

export type TranscriptionRun = {
  id: number
  video_id: string
  provider: string
  status: string
  started_at: string
  finished_at: string | null
  srt_entry_count: number
  validation_summary: string
  fallback_reason: string
}

export type JobRecord = {
  id: string
  pipeline_key: string
  source_id: string
  title: string
  status: string
}

export type JobsResponse = {
  jobs: JobRecord[]
}
