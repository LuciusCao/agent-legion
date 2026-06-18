import { useRef } from 'react'
import { useSettingStore } from '../stores/settingStore'

function getSelectedValue(event: Event): string {
  const custom = event as CustomEvent<{ value?: string }>
  if (custom.detail?.value !== undefined) {
    return custom.detail.value
  }
  return (event.target as HTMLSelectElement | null)?.value ?? ''
}

type SelectRefEntry = {
  el: HTMLElement
  handler: (event: Event) => void
}

export function ExecutorBindingSection() {
  const {
    workflowDefinition,
    executorCatalog,
    executorConfiguration,
    setNodeBinding,
  } = useSettingStore()

  const selectRefs = useRef<Record<string, SelectRefEntry | null>>({})

  const allocatedIds = new Set(
    executorConfiguration.allocations.map((a) => a.executor_id)
  )

  if (!workflowDefinition) return null

  const workflowKey = workflowDefinition.key

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
        节点绑定
      </h3>

      <ul
        style={{
          listStyle: 'none',
          margin: 0,
          padding: 0,
          display: 'grid',
          gap: 12,
        }}
      >
        {workflowDefinition.nodes.map((node) => {
          const currentBinding = executorConfiguration.bindings.find(
            (b) => b.workflow_key === workflowKey && b.node_key === node.key
          )
          const compatibleExecutors = executorCatalog.filter(
            (executor) =>
              allocatedIds.has(executor.id) &&
              executor.capabilities.includes(node.capability)
          )
          const hasSupport = compatibleExecutors.length > 0

          return (
            <li
              key={node.key}
              style={{
                display: 'grid',
                gap: 8,
                padding: 12,
                border: '1px solid var(--md-sys-color-outline-variant)',
                borderRadius: 12,
              }}
            >
              <div
                style={{
                  display: 'flex',
                  alignItems: 'center',
                  gap: 12,
                }}
              >
                <span
                  style={{
                    flex: 1,
                    fontWeight: 500,
                    minWidth: 0,
                    overflow: 'hidden',
                    textOverflow: 'ellipsis',
                    whiteSpace: 'nowrap',
                  }}
                >
                  {node.label}
                </span>
                <span
                  style={{
                    fontSize: 12,
                    padding: '2px 8px',
                    borderRadius: 999,
                    background: 'var(--md-sys-color-surface-variant)',
                    color: 'var(--md-sys-color-on-surface-variant)',
                    flexShrink: 0,
                  }}
                >
                  {node.capability}
                </span>
              </div>

              <md-outlined-select
                ref={(el: HTMLElement | null) => {
                  const prev = selectRefs.current[node.key]
                  if (prev && prev.el !== el) {
                    prev.el.removeEventListener('change', prev.handler)
                    selectRefs.current[node.key] = null
                  }
                  if (el) {
                    const handler = (event: Event) => {
                      const value = getSelectedValue(event)
                      setNodeBinding(
                        workflowKey,
                        node.key,
                        value === '' ? null : value
                      )
                    }
                    el.addEventListener('change', handler)
                    selectRefs.current[node.key] = { el, handler }
                  }
                }}
                label={`绑定 ${node.key}`}
                aria-label={`绑定 ${node.key}`}
                value={currentBinding?.executor_id ?? ''}
              >
                <md-select-option value="">
                  <div slot="headline">未绑定</div>
                </md-select-option>
                {compatibleExecutors.map((executor) => (
                  <md-select-option key={executor.id} value={executor.id}>
                    <div slot="headline">
                      {executor.id} ({executor.kind})
                    </div>
                  </md-select-option>
                ))}
              </md-outlined-select>

              {!hasSupport && (
                <div
                  style={{
                    fontSize: 12,
                    color: 'var(--md-sys-color-error)',
                  }}
                >
                  没有已分配的执行器支持能力 {node.capability}
                </div>
              )}
            </li>
          )
        })}
      </ul>
    </div>
  )
}
