import { useQuery } from '@tanstack/react-query'
import { useState } from 'react'
import { Navigate, useNavigate } from 'react-router-dom'
import { Button } from '@mui/material'
import { getStudioAgents } from '../api/studioAgents'
import type { StudioAgentRegistryResponse } from '../api/studioAgents'
import { useAuthStore } from '../stores/authStore'
import { extraQueryKeys } from '../lib/queryKeysExtra'
import { toErrorMessage } from '../lib/queryError'
import {
  dismissGlobalOnboarding,
  isGlobalOnboardingDismissed,
} from './GlobalOnboardingPage.storage'
import styles from './GlobalOnboardingPage.module.css'

type StudioAgentEntry = NonNullable<
  StudioAgentRegistryResponse['agents']
>[number]

// M2（#332）会为每个 agent 补充 source/detected 与逐项 availability；当前
// 契约是顶层 availability 映射（id → boolean）。两种形状都接受，合并后
// 以 M2 的逐项字段为准（兜底读取可以随 M2 落地后清退）。
type StudioAgentWithDetection = StudioAgentEntry & {
  detected?: boolean
  source?: string
  availability?: boolean | string
}

function isAgentAvailable(
  agent: StudioAgentWithDetection,
  topLevel: Record<string, boolean>
): boolean {
  const own = agent.availability ?? agent.detected
  if (typeof own === 'boolean') return own
  if (typeof own === 'string') return own === 'available' || own === 'detected'
  return topLevel[agent.id] === true
}

/**
 * #333 全局 onboarding：admin bootstrap 后、进入产品前的极简全局清单。
 * 核心项是 ACP agent 确认（配合 #332 自动探测，确认成本趋近于零）；
 * skill 源等其余实例配置刻意不进清单。任何离开动作都会写 dismissed
 * 标记（localStorage），之后可从全局设置侧栏的「全局初始化清单」回补。
 */
export default function GlobalOnboardingPage() {
  const navigate = useNavigate()
  const currentUser = useAuthStore((s) => s.user)
  // 回补识别：已 dismiss 后经全局设置入口回来时给出状态提示（只读一次）。
  const [wasDismissed] = useState(() => isGlobalOnboardingDismissed())
  const { data, error } = useQuery({
    // 与全局设置「Studio Agent 管理」共享缓存：回补跳转不重复请求。
    queryKey: extraQueryKeys.studioAgents(),
    queryFn: getStudioAgents,
    enabled: currentUser?.role === 'admin',
  })

  if (currentUser?.role !== 'admin') {
    return <Navigate to="/" replace />
  }

  function leave(to: string) {
    dismissGlobalOnboarding()
    navigate(to, { replace: true })
  }

  const agents: StudioAgentWithDetection[] = data?.agents ?? []
  const availability = data?.availability ?? {}
  const availableCount = agents.filter((agent) =>
    isAgentAvailable(agent, availability)
  ).length
  const agentsReady = agents.length > 0 && availableCount === agents.length
  const loadError = toErrorMessage(error)

  return (
    <div className={styles.page}>
      <div className={styles.card}>
        <h1 className={styles.title}>全局初始化清单</h1>
        <p className={styles.hint}>
          进入产品前确认实例级配置。清单只收全局项；workspace 级配置在各
          workspace 内引导。
        </p>
        {wasDismissed && (
          <p className={styles.hint}>
            你之前已完成或跳过该清单，可随时在这里回补确认。
          </p>
        )}
        <section className={styles.item}>
          <div className={styles.itemHeader}>
            <h2 className={styles.itemTitle}>确认 ACP agent</h2>
            {data && (
              <span
                className={
                  agentsReady ? styles.statusReady : styles.statusPending
                }
              >
                {agentsReady ? '已就绪' : '待确认'}
              </span>
            )}
          </div>
          <p className={styles.hint}>
            Studio 对话与 agent 节点执行依赖 ACP agent；探测结果以 PATH
            可用性为准，可稍后在全局设置的「Studio Agent 管理」调整。
          </p>
          {loadError && (
            <p className={styles.error} role="alert">
              {loadError}
            </p>
          )}
          {!data && !loadError && <p className={styles.hint}>加载中…</p>}
          {data && agents.length === 0 && (
            <p className={styles.hint}>
              尚未探测到可用的 ACP agent，可在全局设置中注册。
            </p>
          )}
          {agents.length > 0 && (
            <ul className={styles.agentList}>
              {agents.map((agent) => {
                const available = isAgentAvailable(agent, availability)
                return (
                  <li key={agent.id} className={styles.agentRow}>
                    <span className={styles.agentLabel}>{agent.label}</span>
                    <code className={styles.agentCommand}>
                      {[agent.command, ...(agent.args ?? [])].join(' ')}
                    </code>
                    {agent.source && (
                      <span className={styles.sourceChip}>{agent.source}</span>
                    )}
                    <span
                      className={
                        available ? styles.statusReady : styles.statusPending
                      }
                    >
                      {available ? '可用' : '不可用'}
                    </span>
                  </li>
                )
              })}
            </ul>
          )}
        </section>
        <div className={styles.actions}>
          <Button variant="contained" onClick={() => leave('/')}>
            进入产品
          </Button>
          <Button variant="text" onClick={() => leave('/admin/settings')}>
            去全局设置
          </Button>
        </div>
      </div>
    </div>
  )
}
