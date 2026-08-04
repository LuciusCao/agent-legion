export interface RunLike {
  node_key: string
  started_at: string
}

export interface NodeLike {
  created_at: string
  node_key?: string
  key?: string
}

function parseUtcDate(value: string | undefined | null): Date | undefined {
  if (!value) return undefined
  // SQLite current_timestamp returns "YYYY-MM-DD HH:MM:SS" (UTC).
  // ISO 8601 timestamps include a "T" and an explicit timezone.
  const normalized = value.includes('T') ? value : `${value.replace(' ', 'T')}Z`
  const parsed = new Date(normalized)
  return Number.isNaN(parsed.getTime()) ? undefined : parsed
}

function getNodeKey(node: NodeLike): string | undefined {
  return node.node_key ?? node.key
}

/**
 * Keep only node runs that happened during the current node incarnation.
 * After a rerun, job_nodes.created_at is refreshed; older runs should not
 * surface their errors or logs for nodes that have been reset.
 */
export function filterRelevantRuns<R extends RunLike, N extends NodeLike>(
  runs: R[],
  nodes: N[]
): R[] {
  const createdAtMs = new Map<string, number>()
  for (const node of nodes) {
    const key = getNodeKey(node)
    if (!key) continue
    const created = parseUtcDate(node.created_at)
    if (created === undefined) continue
    createdAtMs.set(key, created.getTime())
  }
  return runs.filter((run) => {
    const nodeCreatedMs = createdAtMs.get(run.node_key)
    if (nodeCreatedMs === undefined) return true
    const runStarted = parseUtcDate(run.started_at)
    if (runStarted === undefined) return true
    return runStarted.getTime() >= nodeCreatedMs
  })
}
