import { useQuery } from '@tanstack/react-query'
import { Navigate, useNavigate } from 'react-router-dom'
import { Button } from '@mui/material'
import { getStudioAgents } from '../api/studioAgents'
import type { StudioAgentRegistryResponse } from '../api/studioAgents'
import { useAuthStore } from '../stores/authStore'
import { extraQueryKeys } from '../lib/queryKeysExtra'
import { toErrorMessage } from '../lib/queryError'
import { dismissGlobalOnboarding } from './GlobalOnboardingPage.storage'
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
 * #333 全局 onboarding：admin bootstrap 后、进入产品前的欢迎页。核心是
 * 告诉管理员产品的工作方式（和 AI agent 对话搭建功能）并确认检测到的
 * agent；支持 ACP 协议的其他 agent 可跳去全局设置手动添加。任何离开
 * 动作都会写 dismissed 标记（localStorage），回补入口已随设置页侧栏
 * 退役（bootstrap 跳转是唯一正常入口，直接访问 URL 仍可达）。
 */
export default function GlobalOnboardingPage() {
  const navigate = useNavigate()
  const currentUser = useAuthStore((s) => s.user)
  const { data, error } = useQuery({
    // 与全局设置「Studio Agent 管理」共享缓存：跳转过去不重复请求。
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
  const loadError = toErrorMessage(error)

  return (
    <div className={styles.page}>
      <div className={styles.card}>
        <h1 className={styles.title}>连接你的 AI Agent</h1>
        <p className={styles.hint}>
          在产品中，你可以直接和 AI agent 对话来搭建功能。以下是在服务器上
          检测到的 agent；如果你使用其他支持 ACP 协议的 agent，也可以手动 添加。
        </p>
        <section className={styles.item}>
          <div className={styles.itemHeader}>
            <h2 className={styles.itemTitle}>检测到的 agent</h2>
          </div>
          {loadError && (
            <p className={styles.error} role="alert">
              {loadError}
            </p>
          )}
          {!data && !loadError && <p className={styles.hint}>加载中…</p>}
          {data && agents.length === 0 && (
            <p className={styles.hint}>
              未检测到已安装的 agent。可先手动添加条目，或安装内置支持的
              agent（Claude Code、Codex、Kimi）后回到全局设置重新检测。
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
          <p className={styles.actionsHint}>你也可以稍后前往设置页面手动添加</p>
          <Button variant="contained" onClick={() => leave('/')}>
            进入产品
          </Button>
        </div>
      </div>
    </div>
  )
}
