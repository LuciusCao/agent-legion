import { useSettingStore } from '../stores/settingStore'

export function LocalNodeLimitSection() {
  const {
    pipelineDefinition,
    executorCatalog,
    executorConfiguration,
    setNodeLimit,
  } = useSettingStore()

  if (!pipelineDefinition) return null

  const pipelineKey = pipelineDefinition.key
  const allocatedMap = new Map(
    executorConfiguration.allocations.map((a) => [
      a.executor_id,
      a.concurrency_limit,
    ])
  )

  const localBoundNodes = pipelineDefinition.nodes.filter((node) => {
    const binding = executorConfiguration.bindings.find(
      (b) => b.pipeline_key === pipelineKey && b.node_key === node.key
    )
    if (!binding) return false
    const executor = executorCatalog.find((e) => e.id === binding.executor_id)
    return executor?.kind === 'local'
  })

  if (localBoundNodes.length === 0) return null

  return (
    <div>
      <h3
        style={{
          fontSize: 14,
          fontWeight: 500,
          margin: '0 0 12px',
          color: 'var(--md-sys-color-on-surface-variant)',
        }}
      >
        本地节点并发
      </h3>

      <div
        style={{
          display: 'flex',
          flexDirection: 'column',
          gap: 12,
        }}
      >
        {localBoundNodes.map((node) => {
          const binding = executorConfiguration.bindings.find(
            (b) => b.pipeline_key === pipelineKey && b.node_key === node.key
          )
          const max = binding ? (allocatedMap.get(binding.executor_id) ?? 1) : 1
          const limit = executorConfiguration.node_limits.find(
            (l) => l.pipeline_key === pipelineKey && l.node_key === node.key
          )

          return (
            <div
              key={node.key}
              style={{
                display: 'flex',
                alignItems: 'center',
                gap: 12,
              }}
            >
              <span style={{ fontSize: 14, minWidth: 120 }}>{node.label}</span>
              <md-outlined-text-field
                type="number"
                min={1}
                max={max}
                label={`${node.label} 并发上限`}
                aria-label={`${node.label} 并发上限`}
                value={limit?.concurrency_limit ?? ''}
                onInput={(event: Event) => {
                  const raw = (event.target as HTMLInputElement).value
                  const value = Number(raw)
                  setNodeLimit(
                    pipelineKey,
                    node.key,
                    raw === '' || Number.isNaN(value) ? null : value
                  )
                }}
                style={{ width: 140 }}
              />
              <span
                style={{
                  fontSize: 12,
                  color: 'var(--md-sys-color-on-surface-variant)',
                }}
              >
                上限: {max}
              </span>
            </div>
          )
        })}
      </div>
    </div>
  )
}
