import type { components } from './generated/api'

type ApiSchemas = components['schemas']

export type WorkspaceStats = ApiSchemas['WorkspaceStatsResponse']
export type ExecutorRuntimeStatus =
  WorkspaceStats['executor_status']['executors'][number]
