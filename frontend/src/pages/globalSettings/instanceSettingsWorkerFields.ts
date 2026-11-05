// Worker 归档、协议、result 提交与产物下载的实例级限制。

import type { FieldGroup } from './instanceSettingsFieldTypes'

export const WORKER_FIELD_GROUPS: FieldGroup[] = [
  {
    title: 'Worker 限制',
    fields: [
      {
        path: 'agent_workers.max_archive_bytes',
        label: '归档大小上限（字节）',
        integer: true,
      },
      {
        path: 'agent_workers.min_protocol_version',
        label: '最低协议版本',
        integer: true,
      },
      {
        path: 'agent_workers.max_concurrent_result_commits',
        label: 'result 提交并发上限（0 不限制）',
        integer: true,
        allowZero: true,
      },
      {
        path: 'agent_workers.artifact_spot_check_percent',
        label: '产物校验抽检比例 %（0 全信任，100 全核验）',
        integer: true,
        allowZero: true,
        max: 100,
      },
      {
        // #739 codex P2：契约 ge=60 / le=604800（S3 SigV4 7 天上限）在客户端
        // 拦截，越界值不再裸奔到后端 422。
        path: 'agent_workers.artifact_download_presign_ttl_seconds',
        label: '外部产物下载直连 URL 有效期（秒）',
        integer: true,
        min: 60,
        max: 604800,
      },
    ],
    toggles: [],
  },
]
