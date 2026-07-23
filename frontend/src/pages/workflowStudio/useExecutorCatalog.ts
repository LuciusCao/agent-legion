import { useEffect, useState } from 'react'
import { getExecutorCatalog } from '../../api/executorApi'
import type { ExecutorDefinition } from '../../types/executorTypes'

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
