import { useQuery } from '@tanstack/react-query'
import { fetchAgentRuntimes } from '../../../api'
import { queryKeys } from '../../../lib/queryKeys'
import type { AgentRuntime, AgentRuntimeToolEntry } from '../../../types'

/**
 * #476：per-runtime 工具目录（runtime adapter 静态声明，与 dispatch 校验
 * 同源）。目录只在发版时变，长 staleTime；挂载即取（无 workspace 依赖）。
 */
export function useAgentRuntimes() {
  return useQuery({
    queryKey: queryKeys.agentRuntimes(),
    queryFn: fetchAgentRuntimes,
    staleTime: 5 * 60_000,
  })
}

/** 当前 runtime 的目录条目（目录未加载时为 undefined）。 */
export function useRuntimeToolEntries(
  runtime: AgentRuntime
): AgentRuntimeToolEntry[] | undefined {
  const { data } = useAgentRuntimes()
  if (!data) return undefined
  return data.runtimes[runtime]?.tools
}
