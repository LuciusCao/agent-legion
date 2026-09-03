import { useId } from 'react'
import { useQuery } from '@tanstack/react-query'
import { getConnectionKeys } from '../../api/connections'
import { extraQueryKeys } from '../../lib/queryKeysExtra'
import type { ConfigSchemaProperty } from '../../types'

/** 名为 `connection` 的 string 字段挂 datalist；其它字段不挂。 */
export function connectionListProp(key: string, datalistId: string) {
  return key === 'connection' ? { list: datalistId } : undefined
}

/**
 * 拉取外部服务连接 key 列表（key-only 端点，任意登录用户可读，#419）。
 * 与 ConnectionsSection 共享同一 query key 缓存：admin 在全局设置增删连接
 * 后 invalidate，已打开表单的候选自动刷新，多实例合并为一次请求。
 * 端点失败（未登录等）静默降级为空候选，调用方自行回退手写输入。
 */
export function useConnectionKeys(): { options: string[]; ready: boolean } {
  const { data, isError } = useQuery({
    queryKey: extraQueryKeys.connectionKeys(),
    queryFn: getConnectionKeys,
    retry: false,
  })
  // ready：请求成功完成（含空列表）；失败返回 false 让调用方降级手写。
  return { options: data?.keys ?? [], ready: data !== undefined && !isError }
}

/**
 * 节点配置中名为 `connection` 的 string 字段做 datalist 候选（#419 起改用
 * key-only 端点：非 admin 也能拿到候选）。datalistId 经 useId 按表单实例
 * 生成，避免多个表单并存时 datalist id 重复。
 */
export function useConnectionOptions(
  connectionProp: ConfigSchemaProperty | undefined
): { datalistId: string; options: string[] } {
  const datalistId = useId()
  const enabled = connectionProp?.type === 'string' && !connectionProp.enum
  const { data } = useQuery({
    queryKey: extraQueryKeys.connectionKeys(),
    queryFn: getConnectionKeys,
    retry: false,
    enabled,
  })
  const options = enabled ? (data?.keys ?? []) : []
  return { datalistId, options }
}

export function ConnectionKeyDatalist({
  id,
  options,
}: {
  id: string
  options: string[]
}) {
  if (options.length === 0) return null
  return (
    <datalist id={id}>
      {options.map((option) => (
        <option key={option} value={option} />
      ))}
    </datalist>
  )
}
