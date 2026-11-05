import type { DagNodeData } from './dagNodeTypes'

/** 类型徽标文案：agent 节点声明了 execution.runtime 时附 runtime
 * （agent·velites——旧「Pi Agent」标签真正想表达的语义，#1180）。 */
export function nodeExecutionTypeLabel(
  data: Pick<DagNodeData, 'executionType' | 'executionRuntime'>
): string | null {
  if (!data.executionType) return null
  if (data.executionType === 'agent' && data.executionRuntime) {
    return `agent·${data.executionRuntime}`
  }
  return data.executionType
}
