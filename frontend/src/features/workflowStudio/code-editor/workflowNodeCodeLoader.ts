import { useCallback, useEffect, useRef, useState } from 'react'
import { api } from '../../../api'
import type { components } from '../../../generated/api'

export type NodeCodeResponse = components['schemas']['WorkflowNodeCodeResponse']
type LoadState = 'loading' | 'ready' | 'error'

export function useWorkflowNodeCodeLoader(
  url: string | null,
  codeBound: boolean
) {
  const [loadState, setLoadState] = useState<LoadState>('loading')
  const [data, setData] = useState<NodeCodeResponse | null>(null)
  const [error, setError] = useState('')

  // #749 修（codex #756 P2）：reload 请求代次。每次发起 reload 递增并捕获
  // 当次序号；响应到达时序号已非最新 ⇒ 该请求发起于更早的状态（如上一次
  // 保存之前），整体丢弃——无论其内容看起来多新，它代表的快照已经过时，
  // 最新状态由后续 reload 或保存回填负责。版本比较识别不了这种滞留：连续
  // 保存同一份已有草稿时 save_draft 原地更新草稿行、draft_version 不递增，
  // 旧响应版本相等，`<` 判 false，会把保存回填的新 hash 打回旧值，发布带
  // 旧 expected_hash 稳定 409。「他端更新」不受误杀：那种场景本会话最后
  // 发出的 reload 就是最新代次，只有被更新的 reload 覆盖的旧请求才丢。
  const reloadGenerationRef = useRef(0)

  // WorkflowNodeCodeSection is keyed by node in the inspector, so this effect
  // only runs on mount (and after explicit reloads via its own calls).
  const reload = useCallback(() => {
    if (!url || !codeBound) return undefined
    const generation = ++reloadGenerationRef.current
    let cancelled = false
    api<NodeCodeResponse>(url)
      .then((result) => {
        // cancelled：组件卸载 / url 变更（effect cleanup）；generation：该
        // 响应已被更新的 reload 取代，滞留快照整体作废（codex #756）。
        if (cancelled || generation !== reloadGenerationRef.current) return
        // #749 修（R2 P3，降级为次级防御）：代次只保证「这是最新发出的
        // 请求」，不保证其快照不旧——读侧可能滞后（副本延迟/池化连接的
        // 旧快照），最新 reload 仍可能回 pre-save 形态。函数式合并按
        // draft_version 保留较新一侧的草稿身份（hash / version / code
        // 同属一个保存，一起保留），其余字段以 reload 为准（后台刷新的
        // 本意）；响应侧无草稿（已发布/已回落）或不比本地新时原样采纳。
        setData((prev) =>
          prev?.draft_code_hash &&
          result.has_draft &&
          (result.draft_version ?? 0) < (prev.draft_version ?? 0)
            ? {
                ...result,
                draft_code: prev.draft_code,
                draft_version: prev.draft_version,
                draft_code_hash: prev.draft_code_hash,
              }
            : result
        )
        setLoadState('ready')
      })
      .catch((err: unknown) => {
        if (cancelled || generation !== reloadGenerationRef.current) return
        setError(err instanceof Error ? err.message : '加载失败')
        setLoadState('error')
      })
    return () => {
      cancelled = true
    }
  }, [url, codeBound])
  useEffect(() => reload(), [reload])

  return { loadState, data, setData, error, setError, reload }
}
