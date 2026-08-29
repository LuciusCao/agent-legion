import type { ContentType } from './types'

export const TYPE_LABELS: Record<ContentType, string> = {
  knowledge: '知识点',
  question: '题目',
}

export const STATUS_LABELS: Record<string, string> = {
  missing_url: '未获取到视频',
  queued: '等待中',
  running: '运行中',
  failed: '失败',
  completed: '已完成',
  paused: '已暂停',
  pending: '等待中',
  stale: '已过期',
}

export const INTERACTION_TYPE_LABELS: Record<string, string> = {
  example_practice: '例题试做',
  interaction_summary: '互动小结',
  video_summary: '互动小结',
}

export const JOB_STATUS_LABELS = STATUS_LABELS

export const JOB_SOURCE_TYPE_LABELS: Record<string, string> = {
  question: '题目',
  knowledge: '知识点',
  video: '视频',
}

export const WORKSPACE_LABELS = {
  overview: '概览',
  jobs: '任务',
  runs: '运行记录',
  settings: '设置',
  executors: '执行器',
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
  refresh: '刷新',
  nodes: '节点',
  globalUrl: '服务地址',
  tokenStatus: 'Token 状态',
  tokenConfigured: '已配置',
  tokenNotConfigured: '未配置',
  env: '环境',
  connectionHealth: '连接状态',
  healthy: '健康',
  notChecked: '未检测',
}
