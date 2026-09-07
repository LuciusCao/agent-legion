import { api } from './core'
import type { AgentRuntimesResponse } from '../types'

/**
 * #476：per-runtime 工具目录（按 runtime 嵌套）。同名工具交集不是契约
 * ——description/parameters 随 runtime 走，消费方不得借交集建立跨
 * runtime 统一语义。目录数据与 dispatch 期工具名校验（#449）同源。
 */
export const fetchAgentRuntimes = () =>
  api<AgentRuntimesResponse>('/api/agent-runtimes')
