import { Tooltip } from '@mui/material'
import type { JobSummary } from '../../types/jobTypes'
import styles from './JobClientTokenSource.module.css'

type ClientTokenJob = Pick<
  JobSummary,
  'source_type' | 'source_id' | 'source_base_id' | 'client_token'
>

const SOURCE_LABELS: Record<string, string> = {
  material: '材料',
  bundle: '文件夹',
}

/** #925：client_token 语义说明（详情 tooltip 与列表 title 共用）。 */
export function clientTokenHint(job: ClientTokenJob): string {
  const base = job.source_base_id || job.source_id
  return `同一材料（${base}）以不同 client_token 提交会生成独立 job；同一 client_token 重复提交命中同一 job。`
}

/**
 * #925：带 client_token（#813）的 job 显式展示「来源材料 + client_token」，
 * 让「同一材料的不同提交」可辨认。token 与去作用域的材料 id 都取服务端
 * 解析的只读字段（`client_token` / `source_base_id`），前端不拆 source_id。
 * 无 token 返回 null，调用方保持原展示。
 */
export function JobClientTokenSource({ job }: { job: ClientTokenJob }) {
  if (!job.client_token) return null
  const label = SOURCE_LABELS[job.source_type] ?? job.source_type
  return (
    <span className={styles.root}>
      <span>
        来源{label}：{job.source_base_id || job.source_id}
      </span>
      {' · '}
      <Tooltip title={clientTokenHint(job)} describeChild arrow>
        <span className={styles.token} tabIndex={0}>
          client_token：{job.client_token}
        </span>
      </Tooltip>
    </span>
  )
}
