import { api } from './core'
import type { components } from '../generated/api'

export type OpsMetricsResponse = components['schemas']['OpsMetricsResponse']

export type MetricBucket = components['schemas']['MetricBucket']

export type OpsGranularity = OpsMetricsResponse['granularity']

export interface OpsMetricsParams {
  granularity: OpsGranularity
  worker_id?: string
}

export async function fetchOpsMetrics(
  params: OpsMetricsParams
): Promise<OpsMetricsResponse> {
  const query = new URLSearchParams({ granularity: params.granularity })
  if (params.worker_id) query.set('worker_id', params.worker_id)
  return api<OpsMetricsResponse>(`/api/metrics/overview?${query.toString()}`)
}
