export type MutationCounts = {
  succeeded: number
  skipped: number
  failed: number
}

export function countMutationResults(
  results: { status: 'succeeded' | 'skipped' | 'failed' }[]
): MutationCounts {
  return results.reduce(
    (acc, r) => {
      if (r.status === 'succeeded') acc.succeeded += 1
      else if (r.status === 'skipped') acc.skipped += 1
      else if (r.status === 'failed') acc.failed += 1
      return acc
    },
    { succeeded: 0, skipped: 0, failed: 0 }
  )
}
export function makeMutationToast(
  action: string,
  counts: MutationCounts
): string {
  if (counts.skipped === 0 && counts.failed === 0) {
    return `${action}完成：成功 ${counts.succeeded} 项`
  }
  if (counts.failed === 0) {
    return `${action}完成：成功 ${counts.succeeded} 项，跳过 ${counts.skipped} 项`
  }
  return `${action}完成：成功 ${counts.succeeded} 项，跳过 ${counts.skipped} 项，失败 ${counts.failed} 项`
}

export function normalizeJobStatus(
  status: string
): 'pending' | 'running' | 'completed' | 'failed' | 'paused' {
  switch (status) {
    case 'pending':
    case 'running':
    case 'completed':
    case 'failed':
    case 'paused':
      return status
    default:
      return 'pending'
  }
}
