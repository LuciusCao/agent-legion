import { useState } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { extraQueryKeys } from '../../lib/queryKeysExtra'
import { useUiStore } from '../../stores/uiStore'
import { redetectStudioAgents } from '../../api/studioAgents'
import type {
  StudioAgentDetection,
  StudioAgentRegistryEntry,
  StudioAgentRegistryResponse,
} from '../../api/studioAgents'
import styles from '../GlobalSettingsPage.module.css'

// StudioAgentsSection 的展示与序列化助手（主文件体积预算拆出）：行模型、
// 可用性/来源徽标、目录探测状态单元格与「重新检测」按钮（#332）。

export interface AgentRow {
  id: string
  label: string
  command: string
  argsText: string
  source?: StudioAgentRegistryEntry['source']
}

export function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error)
}

export function toRows(document: StudioAgentRegistryResponse): AgentRow[] {
  return (document.agents ?? []).map((agent) => ({
    id: agent.id,
    label: agent.label,
    command: agent.command,
    argsText: (agent.args ?? []).join(' '),
    source: agent.source,
  }))
}

export function serialize(apiBase: string, rows: AgentRow[]): string {
  return JSON.stringify({ apiBase: apiBase.trim(), rows })
}

/**
 * #355：编辑器「前进」原语（保存/重检测/409 刷新三路共用）——rows/baseline/
 * apiBase/availability/detection/revision 一次性对齐服务端文档；编辑器状态
 * 只在挂载与这些动作时前进（invalidate 重取的数据不会被已挂载的 useState
 * 编辑器消费，409 刷新若只 invalidate 会陷入二次 409 死循环，旧审核 P1）。
 */
export function useApplyRegistryResult(setters: {
  setApiBase: (v: string) => void
  setRows: (rows: AgentRow[]) => void
  setBaseline: (v: string) => void
  setAvailability: (v: Record<string, boolean>) => void
  setDetection: (v: Record<string, StudioAgentDetection>) => void
  setRevision: (v: string) => void
}) {
  const queryClient = useQueryClient()
  return (result: StudioAgentRegistryResponse) => {
    queryClient.setQueryData(extraQueryKeys.studioAgents(), result)
    const nextRows = toRows(result)
    // apiBase 必须随快照一并前进：漏更会让刷新后立即 dirty，下次保存把旧
    // 地址写回、覆盖并发修改。
    setters.setApiBase(result.api_base)
    setters.setRows(nextRows)
    setters.setBaseline(serialize(result.api_base, nextRows))
    setters.setAvailability(result.availability ?? {})
    setters.setDetection(result.detection ?? {})
    setters.setRevision(result.revision ?? '')
  }
}

export function availabilityBadge(
  availability: Record<string, boolean>,
  id: string
) {
  const value = availability[id.trim()]
  if (value === undefined) return '—'
  return value ? '可用' : <span className={styles.staleBadge}>不可用</span>
}

export function DetectionCell({
  source,
  status,
}: {
  source?: StudioAgentRegistryEntry['source']
  status?: StudioAgentDetection
}) {
  const badge = source === 'detected' ? '自动检测' : '手工'
  if (!status) return <>{badge}</>
  if (!status.detected) {
    return (
      <>
        {badge} · <span className={styles.staleBadge}>未检测到</span>
      </>
    )
  }
  const detail = status.version ?? status.path ?? ''
  return (
    <span title={status.path ?? undefined}>
      {badge} · 已检测到{detail ? `（${detail}）` : ''}
    </span>
  )
}

export function RedetectButton({
  disabled,
  onDone,
  onError,
}: {
  disabled: boolean
  onDone: (result: StudioAgentRegistryResponse) => void
  onError: (message: string) => void
}) {
  const queryClient = useQueryClient()
  const [detecting, setDetecting] = useState(false)

  async function handleClick() {
    setDetecting(true)
    onError('')
    try {
      const result = await redetectStudioAgents()
      queryClient.setQueryData(extraQueryKeys.studioAgents(), result)
      onDone(result)
      useUiStore.getState().showToast('ACP agent 重新检测完成', 'success')
    } catch (err) {
      onError(errorMessage(err))
    } finally {
      setDetecting(false)
    }
  }

  return (
    <button
      type="button"
      className={styles.textButton}
      disabled={disabled || detecting}
      title="重新探测本机已安装的 ACP agent 并合并进注册表（有未保存修改时请先保存）"
      onClick={() => void handleClick()}
    >
      {detecting ? '检测中…' : '重新检测'}
    </button>
  )
}
