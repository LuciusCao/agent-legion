import type { components } from '../generated/api'
import { formatDateTime } from '../lib/formatters'
import styles from './NodeDetailsPanel.module.css'

type LatestRun = Pick<
  components['schemas']['NodeRunResponse'],
  | 'id'
  | 'status'
  | 'started_at'
  | 'exit_code'
  | 'error_message'
  | 'runner'
  | 'skill_version'
>

/** 节点详情面板的「最近运行」卡片（自 NodeDetailsPanel 拆出，文件预算）：
 * run 概要 + runner/skill_version（#410 实际执行版本，此前仅 token 采样内部
 * 可见）/开始时间/退出码/错误信息。 */
export function NodeRunCard({ run }: { run: LatestRun }) {
  return (
    <div className={styles.runCard}>
      <div>
        Run #{run.id} · {run.status}
      </div>
      {run.runner && <div className={styles.muted}>Runner:{run.runner}</div>}
      {run.skill_version && (
        <div className={styles.muted}>Skill 版本：{run.skill_version}</div>
      )}
      <div className={styles.muted}>开始：{formatDateTime(run.started_at)}</div>
      {run.exit_code !== null && <div>退出码：{run.exit_code}</div>}
      {run.error_message && (
        <div className={styles.error}>{run.error_message}</div>
      )}
    </div>
  )
}
