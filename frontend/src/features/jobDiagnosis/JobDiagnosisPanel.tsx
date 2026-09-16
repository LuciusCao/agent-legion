import { useMemo, useState } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { rerunJob, runToJob } from '../../api/jobApi'
import { queryKeys } from '../../lib/queryKeys'
import { AgentChatPanel } from '../workflowStudio/chat/AgentChatPanel'
import shellStyles from '../workflowStudio/chat/AgentChatPanel.module.css'
import {
  latestJobActionSuggestions,
  suggestionKey,
  type JobActionSuggestion,
  type JobDiagnosisTarget,
} from './jobDiagnosisContext'
import { useJobDiagnosis } from './useJobDiagnosis'
import {
  JobDiagnosisActionCard,
  type ActionCardState,
} from './JobDiagnosisActionCard'
import styles from './JobDiagnosisPanel.module.css'

type Props = {
  workspaceId: string
  target: JobDiagnosisTarget
}

/** 排查对话面板（#329）：复用 Studio 对话的全部传输与渲染件，差别只在
 * 会话引导（自动绑定 job/node 上下文）与动作确认卡片（agent 建议 → 人确认
 * → 宿主会话执行 rerunJob/runToJob）。外壳复用 AgentChatPanel 骨架（#695）。 */
export function JobDiagnosisPanel({ workspaceId, target }: Props) {
  const queryClient = useQueryClient()
  const { chat, bootstrapError, retryBootstrap } = useJobDiagnosis(
    workspaceId,
    target
  )
  const [cardStates, setCardStates] = useState<Record<string, ActionCardState>>(
    {}
  )

  const suggestions = useMemo(
    () => latestJobActionSuggestions(chat.messages, target.jobId),
    [chat.messages, target.jobId]
  )
  // 卡片一旦产出（confirmed/dismissed）就保持渲染终态；新建议替换旧建议
  // （latestJobActionSuggestions 只取最后一条带建议的消息）。
  const visibleSuggestions = suggestions.filter(
    (suggestion) => cardStates[suggestionKey(suggestion)]?.phase !== 'dismissed'
  )

  function setCardState(key: string, state: ActionCardState) {
    setCardStates((current) => ({ ...current, [key]: state }))
  }

  async function confirm(suggestion: JobActionSuggestion) {
    const key = suggestionKey(suggestion)
    setCardState(key, { phase: 'executing' })
    try {
      if (suggestion.action === 'rerun_node') {
        await rerunJob(suggestion.jobId, suggestion.nodeKey)
      } else {
        await runToJob(suggestion.jobId, suggestion.nodeKey)
      }
      setCardState(key, { phase: 'done' })
      // 底层页面（job 详情/列表）的缓存失效，回到页面即可验证。
      await queryClient.invalidateQueries({
        queryKey: queryKeys.jobDetail(suggestion.jobId),
      })
    } catch (error) {
      setCardState(key, {
        phase: 'failed',
        error: error instanceof Error ? error.message : '操作失败',
      })
    }
  }

  function dismiss(suggestion: JobActionSuggestion) {
    setCardState(suggestionKey(suggestion), { phase: 'dismissed' })
  }

  if (chat.agentsError) {
    return (
      <div className={shellStyles.emptyState}>
        Agent 列表加载失败，请稍后重试
      </div>
    )
  }
  if (!chat.agentsLoading && chat.agents.length === 0) {
    return (
      <div className={shellStyles.emptyState}>
        未检测到可用的 ACP agent，请联系管理员配置
      </div>
    )
  }

  return (
    <AgentChatPanel
      chat={chat}
      workspaceId={workspaceId}
      className={styles.chatShell}
      bootstrapError={
        bootstrapError
          ? {
              message: `排查会话创建失败：${bootstrapError}`,
              onRetry: retryBootstrap,
            }
          : null
      }
      actionErrorTone="warning"
      emptyState="正在创建排查会话…"
      noSessionReason="正在创建排查会话…"
      closedReason="会话已关闭或出错，点「继续对话」恢复（上下文保留）"
      actionArea={
        visibleSuggestions.length > 0 ? (
          <div className={styles.actionArea}>
            {visibleSuggestions.map((suggestion) => (
              <JobDiagnosisActionCard
                key={suggestionKey(suggestion)}
                suggestion={suggestion}
                state={
                  cardStates[suggestionKey(suggestion)] ?? { phase: 'pending' }
                }
                onConfirm={(item) => void confirm(item)}
                onDismiss={dismiss}
              />
            ))}
          </div>
        ) : null
      }
      // 排查面板没有画布可承接 workflow 草稿；agent 若仍产草稿，
      // 草稿在 Studio 画布流程里照样可审。
      onApplyWorkflowDraft={() => undefined}
    />
  )
}
