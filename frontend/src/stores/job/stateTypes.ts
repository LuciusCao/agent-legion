export type ContinueJobResult = Promise<{
  job_id: string
  operation: string
  status: string
  message?: string | null
  node_key?: string | null
  reason_code?: string | null
}>
