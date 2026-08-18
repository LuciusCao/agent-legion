import { useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import {
  Button,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  TextField,
} from '@mui/material'
import { copyAgent, fetchAgentDefinitions } from '../../api'
import type { AgentListItem } from '../../types'
import { extraQueryKeys } from '../../lib/queryKeysExtra'
import { toErrorMessage } from '../../lib/queryError'
import { useSettingStore } from '../../stores/settingStore'
import { AgentEditor } from './AgentEditor'
import styles from './AgentsPanel.module.css'

const statusLabels: Record<AgentListItem['status'], string> = {
  draft: '草稿',
  published: '已发布',
  archived: '已归档',
}

/**
 * Agent 定义管理面板（Studio 全局对话框）：左侧列表 + 新建/复制，右侧
 * AgentEditor 负责草稿编辑、发布、归档与版本回滚。读写限定当前 workspace。
 */
export function AgentsPanel(props: { initialSelectedId?: string | null }) {
  const queryClient = useQueryClient()
  const workspaceId = useSettingStore((s) => s.workspaceId) ?? undefined
  const {
    data,
    isPending: loading,
    error: queryError,
  } = useQuery({
    queryKey: extraQueryKeys.agentDefinitions(workspaceId ?? ''),
    queryFn: () => fetchAgentDefinitions(workspaceId!),
    enabled: Boolean(workspaceId),
  })
  const error = toErrorMessage(queryError)
  const agents = data?.agents ?? []
  const [selectedId, setSelectedId] = useState<string | null>(
    props.initialSelectedId ?? null
  )
  const [creating, setCreating] = useState(false)
  const [copySource, setCopySource] = useState<AgentListItem | null>(null)
  const [copyTarget, setCopyTarget] = useState('')
  const [copyError, setCopyError] = useState('')

  // 仅在编辑器/复制动作触发（两者都要求 workspaceId 存在）。
  function refresh() {
    void queryClient.invalidateQueries({
      queryKey: extraQueryKeys.agentDefinitions(workspaceId ?? ''),
    })
    // Agent 发布/归档/回滚改变 capability 路由，Studio 目录同会话失效重取。
    void queryClient.invalidateQueries({
      queryKey: extraQueryKeys.studioExecutorCatalog(workspaceId ?? ''),
    })
  }
  const handleSelect = (agentId: string) => {
    setCreating(false)
    setSelectedId(agentId)
  }

  async function handleCopy() {
    const newAgentId = copyTarget.trim()
    if (!copySource || !newAgentId || !workspaceId) return
    setCopyError('')
    try {
      await copyAgent(workspaceId, copySource.agent_id, newAgentId)
      setCopySource(null)
      setCopyTarget('')
      refresh()
      setCreating(false)
      setSelectedId(newAgentId)
    } catch (err) {
      setCopyError(err instanceof Error ? err.message : '复制失败')
    }
  }

  return (
    <div className={styles.layout}>
      <div className={styles.list}>
        <div className={styles.listToolbar}>
          <Button
            size="small"
            variant="outlined"
            onClick={() => {
              setCreating(true)
              setSelectedId(null)
            }}
          >
            新建
          </Button>
        </div>
        {error && (
          <p className={styles.error} role="alert">
            {error}
          </p>
        )}
        {!workspaceId && <p className={styles.hint}>请先选择 Workspace</p>}
        {workspaceId && loading && <p className={styles.hint}>加载中...</p>}
        {workspaceId && !loading && agents.length === 0 && !error && (
          <p className={styles.empty}>暂无 Agent 定义</p>
        )}
        <ul className={styles.listItems}>
          {agents.map((agent) => (
            <li key={agent.agent_id}>
              <button
                type="button"
                className={
                  agent.agent_id === selectedId
                    ? styles.listItemActive
                    : styles.listItem
                }
                onClick={() => handleSelect(agent.agent_id)}
              >
                <span className={styles.listItemTitle}>{agent.agent_id}</span>
                <span className={styles.listItemMeta}>
                  <span>{agent.capability}</span>
                  <span>{agent.runtime}</span>
                  <span>{statusLabels[agent.status]}</span>
                  {agent.has_draft && <span>有草稿</span>}
                </span>
              </button>
              <Button
                size="small"
                onClick={() => {
                  setCopySource(agent)
                  setCopyTarget('')
                  setCopyError('')
                }}
              >
                复制
              </Button>
            </li>
          ))}
        </ul>
      </div>
      <div className={styles.editor}>
        {workspaceId && (creating || selectedId) ? (
          <AgentEditor
            key={creating ? '__new__' : selectedId}
            workspaceId={workspaceId}
            agentId={creating ? null : selectedId}
            onSaved={(agentId) => {
              refresh()
              setCreating(false)
              setSelectedId(agentId)
            }}
            onChanged={refresh}
            onArchived={() => {
              refresh()
              setSelectedId(null)
            }}
          />
        ) : (
          <p className={styles.empty}>请选择左侧 Agent，或点击「新建」。</p>
        )}
      </div>

      <Dialog
        open={copySource !== null}
        onClose={() => setCopySource(null)}
        maxWidth="xs"
        fullWidth
      >
        <DialogTitle>复制 Agent「{copySource?.agent_id}」</DialogTitle>
        <DialogContent>
          <TextField
            label="新 Agent ID"
            variant="outlined"
            value={copyTarget}
            onChange={(e) => setCopyTarget(e.target.value)}
            fullWidth
            sx={{ mt: 1 }}
          />
          {copyError && (
            <p className={styles.error} role="alert">
              {copyError}
            </p>
          )}
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setCopySource(null)}>取消</Button>
          <Button
            variant="contained"
            onClick={() => void handleCopy()}
            disabled={copyTarget.trim() === ''}
          >
            复制
          </Button>
        </DialogActions>
      </Dialog>
    </div>
  )
}
