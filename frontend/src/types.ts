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
  workspace_id: string
  busy: boolean
  task_count: number
  max_tasks: number
  current_video_id: string | null
  current_title?: string
  current_content_type?: ContentType | ''
  current_external_id?: string
  current_phase?: string
}

export type WorkspaceAgentAssignment = {
  agent_id: string
  concurrency_limit: number
}

export type GlobalServiceStatus = {
  cms: {
    baseUrl: string
    tokenConfigured: boolean
    env: string
    healthy: boolean | null
    lastCheckedAt: string | null
  }
}

export type ResourceProviderDefinition = {
  key: string
  provider: string
  path: string
  defaultParams: Record<string, string>
  paramKeys: string[]
}

export type ResourceBinding = {
  enabled: boolean
  config: Record<string, string>
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
  workspace_id: string
  pipeline_key: string
  source_id: string
  source_type?: string
  title: string
  stem: string
  status: string
  created_at?: string
  completed_nodes?: number
  total_nodes?: number
}

export type JobsResponse = {
  jobs: JobRecord[]
}

export type WorkspaceRecord = {
  id: string
  name: string
  description?: string
  default_pipeline_key: string
  default_entity: string
  cms_config?: Record<string, unknown>
  resource_config?: Record<string, unknown>
  intake_config?: {
    enabled_modes?: string[]
    label_overrides?: Record<string, string>
  }
}

export type WorkspaceSettings = {
  entityType: 'question' | 'knowledge' | 'video'
  intakeModes: string[]
  labelOverrides: Record<string, string>
  pipelineKey: string
  agentIds: string[]
  concurrencyLimit: number
  resources: Record<string, ResourceBinding>
  cmsUrl?: string
  cmsToken?: string
  localConcurrency?: number
  agentConcurrency?: number
  nodeLocalConcurrency?: Record<string, number>
}

export type PipelineNodeRecord = {
  key: string
  label: string
  runner: 'local' | 'agent'
  after: string[]
  inputs: string[]
  outputs: string[]
}

export type PipelineIntakeModeRecord = {
  key: string
  label: string
  input_field: string
  resource: string
}

export type PipelineDefinitionRecord = {
  key: string
  label: string
  concurrency: {
    local: number
    agent: number
    nodes: Record<string, number>
  }
  intake?: {
    modes: PipelineIntakeModeRecord[]
  }
  nodes: PipelineNodeRecord[]
}

export type PipelineResponse = {
  pipeline: PipelineDefinitionRecord
}

export type PipelinesListResponse = {
  pipelines: Array<{
    key: string
    label: string
    concurrency: { local: number; agent: number; nodes: Record<string, number> }
  }>
}

export type JobBatchResponse = {
  batch: Record<string, unknown>
  created_count: number
  jobs: JobRecord[]
}

export type JobNodeRecord = {
  id: number
  job_id: string
  node_key: string
  label: string
  status: string
  after?: string[]
  stale_reason?: string
  error_message?: string
  started_at?: string | null
  finished_at?: string | null
}

export type NodeRunRecord = {
  id: number
  job_id: string
  node_key: string
  status: string
  command_json: string
  exit_code: number | null
  log_path: string
  error_message: string
  started_at: string
  finished_at: string | null
  run_dir: string
  session_dir: string
}

export type JobDetailResponse = {
  job: JobRecord
  nodes: JobNodeRecord[]
  runs: NodeRunRecord[]
  artifacts: string[]
}

export type ArtifactResponse = {
  name: string
  content: string
}

export type CreateJobBatchInput = {
  workspaceId: string
  pipelineKey?: string
  entity?: string
  sourceKind: string
  inputField: string
  values: string[]
}

export type QuestionOption = Record<string, unknown>

export type QuestionNormalized = {
  stem?: string
  options?: QuestionOption[]
  answer?: unknown
  analysis?: unknown
}

export type QuestionDetailResponse = {
  question_id: string
  title: string
  normalized: QuestionNormalized
  cms_payload: Record<string, unknown> | null
  jobs: JobRecord[]
}

export type WorkspacesResponse = {
  workspaces: WorkspaceRecord[]
}

export type WorkspaceStats = {
  workspace_id: string
  name: string
  pipeline_key: string
  pipeline_label: string
  job_stats: Record<string, number>
  agent_status: {
    total: number
    busy: number
    idle: number
    agents?: Array<{ id: string; name: string; busy: boolean }>
  }
  latest_run: {
    node_key: string
    status: string
    started_at: string
  } | null
}
