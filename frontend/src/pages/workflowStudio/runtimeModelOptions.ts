import { useMemo } from 'react'
import type { WorkspaceRuntimeModelsResponse } from '../../types'

type RuntimeModels = WorkspaceRuntimeModelsResponse['runtimes']

/**
 * 该节点 agent.runtime 可见的 provider → models 映射：在线 Worker 声明的
 * 精确 runtime 条目加上对所有 runtime 生效的 '*' 通配条目。
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
      const bucket = (merged[provider] ??= new Set())
      for (const model of models) bucket.add(model)
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
