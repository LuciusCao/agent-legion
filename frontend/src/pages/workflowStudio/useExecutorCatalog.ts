import { useEffect, useState } from 'react'
import { getExecutorCatalog } from '../../executorApi'
import type { ExecutorDefinition } from '../../executorTypes'

export function useExecutorCatalog() {
  const [executorCatalog, setExecutorCatalog] = useState<ExecutorDefinition[]>(
    []
  )
  useEffect(() => {
    void getExecutorCatalog()
      .then((catalog) => setExecutorCatalog(catalog.executors))
      .catch(() => setExecutorCatalog([]))
  }, [])
  return executorCatalog
}
