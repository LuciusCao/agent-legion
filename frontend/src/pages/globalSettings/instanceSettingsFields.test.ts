import { describe, expect, it } from 'vitest'
import type { InstanceSettingsResponse } from '../../api/instanceSettings'
import { FIELD_GROUPS, fieldDef } from './instanceSettingsFields'
import { toFormValues } from './instanceSettingsFormValues'
import { buildPayload } from './instanceSettingsPayload'

const settings: InstanceSettingsResponse = {
  cleanup: {
    log_retention_days: 30,
    run_dir_retention_days: 7,
    interval_seconds: 3600,
  },
  monitoring: { sample_interval_seconds: 15, retention_days: 30 },
  heartbeat_interval_seconds: 10,
  lease_ttl_seconds: 90,
  heartbeat_failure_threshold: 3,
  sweeper_enabled: true,
  sweeper_interval_seconds: 60,
  code_capacity: 16,
  materials_ttl_days: 0,
  execution_retention_days: 0,
  studio_chat_retention_days: 0,
  studio_chat_terminal_grant_required: false,
  workflows: { max_items_per_run: 20000, node_code_max_bytes: 65536 },
  agent_workers: {
    max_archive_bytes: 104857600,
    min_protocol_version: 2,
    max_concurrent_result_commits: 16,
    result_commit_batching: true,
    artifact_spot_check_percent: 3,
    artifact_download_presign_ttl_seconds: 3600,
  },
  agent_enqueue: { workers: 48, max_pending: 1024 },
  result_unpack: { workers: 0 },
  result_validate: { workers: 0 },
  agent_claim: { worker_touch_interval_seconds: 30 },
  csp_script_unsafe_inline: false,
  skills_root: '~/.agents/skills',
}

describe('instance settings field modules', () => {
  it('preserves every group, field path, toggle and display order', () => {
    expect(
      FIELD_GROUPS.map((group) => ({
        title: group.title,
        fields: group.fields.map((field) => field.path),
        toggles: group.toggles.map((toggle) => toggle.path),
      }))
    ).toEqual([
      {
        title: '清理',
        fields: [
          'cleanup.log_retention_days',
          'cleanup.run_dir_retention_days',
          'cleanup.interval_seconds',
        ],
        toggles: [],
      },
      {
        title: '监控',
        fields: [
          'monitoring.sample_interval_seconds',
          'monitoring.retention_days',
        ],
        toggles: [],
      },
      {
        title: '心跳与租约',
        fields: [
          'heartbeat_interval_seconds',
          'lease_ttl_seconds',
          'heartbeat_failure_threshold',
        ],
        toggles: [],
      },
      {
        title: 'Sweeper',
        fields: ['sweeper_interval_seconds'],
        toggles: ['sweeper_enabled'],
      },
      {
        title: '运行与本地执行',
        fields: [
          'workflows.max_items_per_run',
          'workflows.node_code_max_bytes',
          'code_capacity',
        ],
        toggles: [],
      },
      {
        title: '材料',
        fields: ['materials_ttl_days'],
        toggles: [],
      },
      {
        title: '执行面保留',
        fields: ['execution_retention_days'],
        toggles: [],
      },
      {
        title: 'Studio 对话保留',
        fields: ['studio_chat_retention_days'],
        toggles: [],
      },
      {
        title: 'Studio 对话终端',
        fields: [],
        toggles: ['studio_chat_terminal_grant_required'],
      },
      {
        title: '队列与解包容量',
        fields: [
          'agent_enqueue.workers',
          'agent_enqueue.max_pending',
          'result_unpack.workers',
          'result_validate.workers',
          'agent_claim.worker_touch_interval_seconds',
        ],
        toggles: ['agent_workers.result_commit_batching'],
      },
      {
        title: '安全',
        fields: [],
        toggles: ['csp_script_unsafe_inline'],
      },
      {
        title: 'Worker 限制',
        fields: [
          'agent_workers.max_archive_bytes',
          'agent_workers.min_protocol_version',
          'agent_workers.max_concurrent_result_commits',
          'agent_workers.artifact_spot_check_percent',
          'agent_workers.artifact_download_presign_ttl_seconds',
        ],
        toggles: [],
      },
    ])
  })

  it('keeps field lookup and contract bounds attached to the same paths', () => {
    expect(fieldDef('workflows.node_code_max_bytes')).toMatchObject({
      integer: true,
      min: 1,
    })
    expect(fieldDef('agent_enqueue.workers')).toMatchObject({ max: 256 })
    expect(fieldDef('result_unpack.workers')).toMatchObject({
      allowZero: true,
      max: 64,
    })
    expect(
      fieldDef('agent_workers.artifact_download_presign_ttl_seconds')
    ).toMatchObject({ min: 60, max: 604800 })
    expect(() => fieldDef('missing.path')).toThrow(
      'unknown instance settings field: missing.path'
    )
  })

  it('round-trips every generated API field without the read-only skills root', () => {
    const { skills_root, ...expected } = settings

    expect(skills_root).toBe('~/.agents/skills')
    expect(buildPayload(toFormValues(settings))).toEqual(expected)
  })

  it.each([
    ['agent_enqueue.workers', '257', 'Agent 入队线程数 必须不大于 256'],
    [
      'result_validate.workers',
      '65',
      'result 校验进程数（0 = 自动） 必须不大于 64',
    ],
    [
      'agent_workers.artifact_download_presign_ttl_seconds',
      '59',
      '外部产物下载直连 URL 有效期（秒） 必须不小于 60',
    ],
  ])('retains validation for %s', (path, value, message) => {
    expect(() =>
      buildPayload({ ...toFormValues(settings), [path]: value })
    ).toThrow(message)
  })
})
