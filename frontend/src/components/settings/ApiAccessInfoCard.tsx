import type { ApiTokenRateLimit } from '../../api'
import { ApiAccessCopyButton } from './ApiAccessCopyButton'
import { API_TOKEN_PLACEHOLDER } from './apiAccessSnippets'
import styles from './ApiAccess.module.css'

/**
 * 「外部对接」接入参数卡（#870）：workspace_id、API base、鉴权头与当前
 * 生效的 per-token 限流（#738，只读——实例级 env-only 参数）。「复制接入
 * 信息」把这些参数拼成一段纯文本，直接转交给对接方。
 */
export function ApiAccessInfoCard({
  workspaceId,
  apiBase,
  rateLimit,
}: {
  workspaceId: string
  apiBase: string
  rateLimit: ApiTokenRateLimit | undefined
}) {
  const authHeader = `Authorization: Bearer ${API_TOKEN_PLACEHOLDER}`
  const rateLimitText = rateLimit
    ? `每个 Token 每分钟补充 ${rateLimit.requests_per_minute} 次请求，突发容量 ${rateLimit.burst} 次；超限返回 429 + Retry-After`
    : '读取中…'
  const summary = [
    `API Base: ${apiBase}`,
    `Workspace ID: ${workspaceId}`,
    `端点前缀: ${apiBase}/api/workspaces/${workspaceId}`,
    `鉴权: ${authHeader}（Bearer 通道免 CSRF）`,
    ...(rateLimit ? [`限流: ${rateLimitText}`] : []),
    '对接契约: docs/workspace-api-tokens.md',
  ].join('\n')

  return (
    <div className={styles.card}>
      <div className={styles.cardHeader}>
        <h3 className={styles.heading}>接入参数</h3>
        <ApiAccessCopyButton
          text={summary}
          label="复制接入信息"
          ariaLabel="复制接入信息"
        />
      </div>
      <dl className={styles.params}>
        <div className={styles.paramRow}>
          <dt className={styles.paramLabel}>Workspace ID</dt>
          <dd className={`${styles.paramValue} ${styles.mono}`}>
            {workspaceId}
          </dd>
          <ApiAccessCopyButton
            text={workspaceId}
            ariaLabel="复制 Workspace ID"
          />
        </div>
        <div className={styles.paramRow}>
          <dt className={styles.paramLabel}>API Base</dt>
          <dd className={`${styles.paramValue} ${styles.mono}`}>{apiBase}</dd>
          <ApiAccessCopyButton text={apiBase} ariaLabel="复制 API Base" />
        </div>
        <div className={styles.paramRow}>
          <dt className={styles.paramLabel}>鉴权</dt>
          <dd className={styles.paramValue}>
            <code>Authorization: Bearer &lt;API Token&gt;</code>
            ，Bearer 通道无需 CSRF header
          </dd>
        </div>
        <div className={styles.paramRow}>
          <dt className={styles.paramLabel}>请求限流</dt>
          <dd className={styles.paramValue} data-testid="api-rate-limit">
            {rateLimitText}
          </dd>
        </div>
      </dl>
      <p className={styles.hint}>
        API Base
        取自当前控制台地址；外部系统经其他域名或反向代理访问时，以对外可达的地址为准。
        限流为实例级参数（环境变量{' '}
        <code>AGENT_LEGION_API_TOKEN_RATE_LIMIT_PER_MINUTE</code> /{' '}
        <code>AGENT_LEGION_API_TOKEN_RATE_LIMIT_BURST</code>
        ，重启生效），此处只读展示。
      </p>
    </div>
  )
}
