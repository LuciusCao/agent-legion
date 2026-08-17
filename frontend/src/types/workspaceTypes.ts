import type { components } from '../generated/api'

type ApiSchemas = components['schemas']

export type WorkspaceStats = ApiSchemas['WorkspaceStatsResponse']
export type CodePoolStatus = WorkspaceStats['code_pool']
