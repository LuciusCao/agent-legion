import { useEffect, useState } from 'react'
import { getExecutorCatalog } from '../../api/executorApi'
import type {
  AgentDefinition,
  ExecutorDefinition,
} from '../../types/executorTypes'

export function useExecutorCatalog() {
  const [catalog, setCatalog] = useState<
    [ExecutorDefinition[], AgentDefinition[]]
  >([[], []])
  useEffect(() => {
    void getExecutorCatalog()
      .then((result) => setCatalog([result.executors, result.agents ?? []]))
      .catch(() => setCatalog([[], []]))
  }, [])
  return catalog
}
