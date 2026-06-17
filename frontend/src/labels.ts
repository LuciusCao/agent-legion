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

export const JOB_STATUS_LABELS: Record<string, string> = {
  queued: '排队中',
  running: '处理中',
  failed: '失败',
  completed: '已完成',
  pending: '待处理',
}

export const JOB_STATUS_ICONS: Record<string, string> = {
  queued: 'schedule',
  running: 'sync',
  failed: 'error',
  completed: 'check_circle',
  pending: 'radio_button_unchecked',
}

export const WORKSPACE_LABELS = {
  overview: '概览',
  jobs: '任务',
  runs: '运行记录',
  settings: '设置',
  executors: '执行器',
  resources: '资源接口',
  intake: '接入模式',
  workflow: '工作流',
  packages: '打包',
  enter: '进入',
  createWorkspace: '新建 Workspace',
  workspaceName: 'Workspace 名称',
  create: '创建',
  deleteWorkspace: '删除 Workspace',
  confirmDelete: '确认删除',
  videoQueue: '视频队列',
  createJob: '创建任务',
  jobList: '任务列表',
  confirmDeleteJob: '确认删除任务',
  backToJobList: '返回任务列表',
  refresh: '刷新',
  nodes: '节点',
  globalServices: '全局服务',
  resourceProviders: '资源接口',
  providerEnabled: '启用',
  providerParams: '参数配置',
  noResourceProviders: '暂无可用资源接口',
  globalUrl: '服务地址',
  tokenStatus: 'Token 状态',
  tokenConfigured: '已配置',
  tokenNotConfigured: '未配置',
  env: '环境',
  connectionHealth: '连接状态',
  healthy: '健康',
  notChecked: '未检测',
  disabledProviderTooltip: '对应的接入模式将不可用',
}
