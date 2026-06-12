import { useEffect, useMemo } from 'react'
import { useSettingStore } from '../stores/settingStore'
import { ExecutorAllocationRemovalDialog } from './ExecutorAllocationRemovalDialog'

export function ExecutorAllocationSection() {
  const {
    executorCatalog,
    executorConfiguration,
    setExecutorAllocation,
    requestExecutorRemoval,
    pendingAllocationRemoval,
    confirmExecutorRemoval,
  } = useSettingStore()

  const { allocations, bindings } = executorConfiguration

  const affectedBindings = useMemo(() => {
    if (!pendingAllocationRemoval) return []
    return bindings.filter((b) => b.executor_id === pendingAllocationRemoval)
  }, [pendingAllocationRemoval, bindings])

  useEffect(() => {
    if (pendingAllocationRemoval && affectedBindings.length === 0) {
      confirmExecutorRemoval()
    }
  }, [pendingAllocationRemoval, affectedBindings, confirmExecutorRemoval])

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
        执行器分配
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
        {executorCatalog.map((executor) => {
          const allocation = allocations.find(
            (a) => a.executor_id === executor.id
          )
          const isAllocated = !!allocation

          return (
            <li
              key={executor.id}
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
                <md-switch
                  selected={isAllocated || undefined}
                  onClick={() => {
                    if (isAllocated) {
                      requestExecutorRemoval(executor.id)
                    } else {
                      setExecutorAllocation(executor.id, 1)
                    }
                  }}
                  aria-label={`分配 ${executor.id}`}
                />
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
                  {executor.id}
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
                  {executor.kind}
                </span>
              </div>

              <div
                style={{
                  fontSize: 12,
                  color: 'var(--md-sys-color-on-surface-variant)',
                }}
              >
                全局容量: {executor.global_capacity}
              </div>
              <div
                style={{
                  fontSize: 12,
                  color: 'var(--md-sys-color-on-surface-variant)',
                }}
              >
                支持能力: {executor.capabilities.join(', ')}
              </div>

              {isAllocated && (
                <md-outlined-text-field
                  type="number"
                  min={1}
                  max={executor.global_capacity}
                  value={allocation.concurrency_limit}
                  label="工作空间上限"
                  onInput={(event: Event) => {
                    const value = Number(
                      (event.target as HTMLInputElement).value
                    )
                    setExecutorAllocation(
                      executor.id,
                      Number.isNaN(value) ? 1 : value
                    )
                  }}
                  style={{ width: 140 }}
                />
              )}
            </li>
          )
        })}
      </ul>

      {pendingAllocationRemoval && affectedBindings.length > 0 && (
        <ExecutorAllocationRemovalDialog />
      )}
    </div>
  )
}
