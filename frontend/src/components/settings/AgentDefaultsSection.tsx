import { useState } from 'react'
import { Button, TextField } from '@mui/material'
import { updateAgentDefaults } from '../../api'
import { useUiStore } from '../../stores/uiStore'
import type { AgentDefaults } from '../../types'
import styles from '../../pages/SettingsPage.module.css'

type Props = {
  workspaceId: string
  agentDefaults?: AgentDefaults
  onSaved: (agentDefaults: AgentDefaults) => void
}

/**
 * Workspace-level default execution config for Agent nodes. Node-level
 * overrides in Studio win over these defaults; without them Agent nodes
 * cannot be enqueued. Saved through PATCH settings/agent-defaults.
 */
export function AgentDefaultsSection({
  workspaceId,
  agentDefaults,
  onSaved,
}: Props) {
  const [provider, setProvider] = useState(agentDefaults?.provider ?? '')
  const [model, setModel] = useState(agentDefaults?.model ?? '')
  const [thinking, setThinking] = useState(agentDefaults?.thinking ?? '')
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')
  const showToast = useUiStore((s) => s.showToast)

  // Re-seed local fields when the store value arrives/changes (adjusting
  // state during render instead of an effect keeps the lint gate happy).
  const signature = JSON.stringify(agentDefaults ?? {})
  const [loadedSignature, setLoadedSignature] = useState(signature)
  if (signature !== loadedSignature) {
    setLoadedSignature(signature)
    setProvider(agentDefaults?.provider ?? '')
    setModel(agentDefaults?.model ?? '')
    setThinking(agentDefaults?.thinking ?? '')
  }

  async function handleSave() {
    const next: AgentDefaults = {
      provider: provider.trim(),
      model: model.trim(),
      thinking: thinking.trim(),
    }
    setSaving(true)
    setError('')
    try {
      await updateAgentDefaults(workspaceId, next)
      onSaved(next)
      showToast('Agent 默认配置已保存', 'success')
    } catch (err) {
      setError(err instanceof Error ? err.message : '保存失败')
    } finally {
      setSaving(false)
    }
  }

  return (
    <section id="agent-defaults" className={styles.section}>
      <h2 className={styles.sectionTitle}>Agent 默认配置</h2>
      <hr className={styles.sectionDivider} />
      <p style={{ fontSize: 13, color: '#616161', marginTop: 0 }}>
        Agent 节点的默认执行配置。Studio 中节点级覆盖优先于此处的默认值；
        不配置会导致 Agent 节点无法入队。
      </p>
      <div className={styles.field}>
        <TextField
          label="Provider"
          variant="outlined"
          value={provider}
          onChange={(e) => setProvider(e.target.value)}
          placeholder="如 deepseek"
          fullWidth
        />
      </div>
      <div className={styles.field}>
        <TextField
          label="Model"
          variant="outlined"
          value={model}
          onChange={(e) => setModel(e.target.value)}
          placeholder="如 deepseek-v4-flash"
          fullWidth
        />
      </div>
      <div className={styles.field}>
        <TextField
          label="Thinking"
          variant="outlined"
          value={thinking}
          onChange={(e) => setThinking(e.target.value)}
          placeholder="low / medium / high，留空表示 runtime 默认"
          fullWidth
        />
      </div>
      <Button
        variant="contained"
        onClick={() => void handleSave()}
        disabled={saving}
      >
        {saving ? '保存中...' : '保存'}
      </Button>
      {error && (
        <div className="error-text" role="alert" style={{ marginTop: 12 }}>
          {error}
        </div>
      )}
    </section>
  )
}
