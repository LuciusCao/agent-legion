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
