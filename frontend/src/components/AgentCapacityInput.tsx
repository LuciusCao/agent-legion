import { useState } from 'react'
import { useSettingStore } from '../stores/settingStore'

/**
 * Editable workspace-level Agent concurrency cap, saved through the settings
 * page's saveAll flow (workspace configuration PUT).
 *
 * A local draft keeps the field editable while typing; only a valid integer
 * >= 1 propagates to the store. On blur the field snaps back to the last
 * valid value: the backend treats absent/null agent_capacity as "unchanged",
 * so once a value is set there is no clearing path.
 */
export function AgentCapacityInput() {
  const agentCapacity = useSettingStore(
    (state) => state.executorConfiguration.agent_capacity ?? null
  )
  const setAgentCapacity = useSettingStore((state) => state.setAgentCapacity)

  const [capacityDraft, setCapacityDraft] = useState<string | null>(null)
  const capacityText =
    capacityDraft ?? (agentCapacity === null ? '' : String(agentCapacity))

  const handleCapacityChange = (text: string) => {
    setCapacityDraft(text)
    const parsed = Number(text)
    if (text.trim() !== '' && Number.isInteger(parsed) && parsed >= 1) {
      setAgentCapacity(parsed)
    }
  }

  return (
    <div
      style={{
        display: 'flex',
        alignItems: 'center',
        gap: 12,
        margin: '0 0 20px',
      }}
    >
      <input
        type="number"
        min={1}
        step={1}
        aria-label="Agent 并发上限"
        placeholder="不限"
        value={capacityText}
        onChange={(event) => handleCapacityChange(event.target.value)}
        onBlur={() => setCapacityDraft(null)}
        style={{
          width: 96,
          padding: '6px 10px',
          fontSize: 13,
          border: '1px solid #c3c6cf',
          borderRadius: 8,
        }}
      />
      <span style={{ fontSize: 12, color: '#74777f' }}>
        该上限约束本 workspace 全部 Agent 节点跨所有 Worker 的总并发；未设置 =
        不限。
      </span>
    </div>
  )
}
