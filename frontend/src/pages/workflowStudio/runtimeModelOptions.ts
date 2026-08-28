import { useMemo } from 'react'
import type { WorkspaceRuntimeModelsResponse } from '../../types'

type RuntimeModels = WorkspaceRuntimeModelsResponse['runtimes']

/**
 * 该节点 agent.runtime 可见的 provider → models 映射：在线 Worker 声明的
 * 精确 runtime 条目加上对所有 runtime 生效的 '*' 通配条目。
 *
 * 字面 '*' 选项在这里过滤（后端聚合保留 Worker 的原始声明作诊断用途）：
 * '*' provider/model 是「任意值均可 claim」的通配声明，不是可提交的字面值
 * ——选中它会冻结出一个没有任何 Worker 能精确匹配的 execution。声明了
 * 通配 model 的 provider 仍保留在 provider 选项里（具体型号自由输入）。
 */
function optionsForRuntime(
  runtimeModels: RuntimeModels | undefined,
  runtime: string
): Record<string, string[]> {
  const merged: Record<string, Set<string>> = {}
  for (const key of [runtime, '*']) {
    for (const [provider, models] of Object.entries(
      runtimeModels?.[key] ?? {}
    )) {
      if (provider === '*') continue
      const bucket = (merged[provider] ??= new Set())
      for (const model of models) if (model !== '*') bucket.add(model)
    }
  }
  return Object.fromEntries(
    Object.entries(merged).map(([provider, models]) => [
      provider,
      [...models].sort(),
    ])
  )
}

export type RuntimeModelOptions = {
  providerOptions: string[]
  modelOptions: string[]
}

/**
 * 节点执行 Provider/Model 的 datalist 选项。Model 选项跟随当前 provider；
 * 未填 provider 时给出该 runtime 的全部型号。无在线 Worker / 无声明时
 * 选项为空（自由输入仍可用）。
 */
export function useRuntimeModelOptions(
  runtimeModels: RuntimeModels | undefined,
  runtime: string,
  currentProvider: string
): RuntimeModelOptions {
  const providerModels = useMemo(
    () => optionsForRuntime(runtimeModels, runtime),
    [runtimeModels, runtime]
  )
  const providerOptions = Object.keys(providerModels).sort()
  const modelOptions = currentProvider
    ? (providerModels[currentProvider] ?? [])
    : [...new Set(Object.values(providerModels).flat())].sort()
  return { providerOptions, modelOptions }
}
