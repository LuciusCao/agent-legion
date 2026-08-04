import { useEffect, useMemo } from 'react'
import { FormControlLabel, Switch, TextField } from '@mui/material'
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
          color: '#43474e',
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
                border: '1px solid #c3c6cf',
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
                <FormControlLabel
                  control={
                    <Switch
                      checked={isAllocated}
                      onChange={() => {
                        if (isAllocated) {
                          requestExecutorRemoval(executor.id)
                        } else {
                          setExecutorAllocation(executor.id, 1)
                        }
                      }}
                      inputProps={{ 'aria-label': `分配 ${executor.id}` }}
                    />
                  }
                  label={
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
                  }
                  sx={{
                    margin: 0,
                    flex: 1,
                    minWidth: 0,
                    '.MuiFormControlLabel-label': { flex: 1, minWidth: 0 },
                  }}
                />
                <span
                  style={{
                    fontSize: 12,
                    padding: '2px 8px',
                    borderRadius: 999,
                    background: '#f0f0f0',
                    color: '#43474e',
                    flexShrink: 0,
                  }}
                >
                  {executor.kind}
                </span>
              </div>

              <div
                style={{
                  fontSize: 12,
                  color: '#43474e',
                }}
              >
                全局容量: {executor.global_capacity}
              </div>
              <div
                style={{
                  fontSize: 12,
                  color: '#43474e',
                }}
              >
                支持能力: {executor.capabilities.join(', ')}
              </div>

              {isAllocated && (
                <TextField
                  variant="outlined"
                  type="number"
                  inputProps={{ min: 1, max: executor.global_capacity }}
                  value={allocation.concurrency_limit}
                  label="工作空间上限"
                  onChange={(event) => {
                    const value = Number(event.target.value)
                    setExecutorAllocation(
                      executor.id,
                      Number.isNaN(value) ? 1 : value
                    )
                  }}
                  sx={{ width: 140 }}
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
