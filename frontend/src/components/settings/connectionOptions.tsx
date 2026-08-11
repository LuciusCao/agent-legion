import { useId } from 'react'
import { useQuery } from '@tanstack/react-query'
import { getConnections } from '../../api/connections'
import { extraQueryKeys } from '../../lib/queryKeysExtra'
import type { ConfigSchemaProperty } from '../../types'

/** 名为 `connection` 的 string 字段挂 datalist；其它字段不挂。 */
export function connectionListProp(key: string, datalistId: string) {
  return key === 'connection' ? { list: datalistId } : undefined
}

/**
 * 拉取外部服务连接 key 列表，供节点配置中名为 `connection` 的 string 字段做
 * datalist 候选。与 ConnectionsSection 共享同一 query key 缓存：连接变更
 * invalidate 后已打开的表单候选自动刷新，多表单实例合并为一次请求。
 * connections API 是 admin-only，非 admin（403）或任何失败都静默降级为空
 * 候选（字段保持普通文本框）。datalistId 经 useId 按表单实例生成，避免
 * 多个表单并存时 datalist id 重复。
 */
export function useConnectionOptions(
  connectionProp: ConfigSchemaProperty | undefined
): { datalistId: string; options: string[] } {
  const datalistId = useId()
  const enabled = connectionProp?.type === 'string' && !connectionProp.enum
  const { data } = useQuery({
    queryKey: extraQueryKeys.connections(),
    queryFn: getConnections,
    retry: false,
    enabled,
  })
  const options = enabled ? (data?.connections.map((c) => c.key) ?? []) : []
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
