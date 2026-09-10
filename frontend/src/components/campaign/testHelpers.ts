import type { CampaignRecord } from '../../types/campaignTypes'

/** 测试用 campaign record 造数（形状来自 generated CampaignRecord）。 */
export function makeCampaign(
  overrides: Partial<CampaignRecord> = {}
): CampaignRecord {
  return {
    id: 'camp-0001',
    workspace_id: 'ws1',
    mode: 'rerun',
    status: 'running',
    name: '重跑 · 全部失败任务',
    target_spec: { filter: { status: 'failed' }, name: '重跑 · 全部失败任务' },
    progress: { cursor: null, processed: 120 },
    watermark: 30000,
    batch_size: 5000,
    batches_submitted: 2,
    jobs_succeeded: 100,
    jobs_skipped: 15,
    jobs_failed: 5,
    error_message: '',
    created_by: 'user-1',
    created_at: '2026-09-09T01:00:00Z',
    updated_at: '2026-09-09T01:05:00Z',
    finished_at: null,
    ...overrides,
  }
}
