import { useCallback } from 'react'
import type { Dispatch, SetStateAction } from 'react'

/** callback ref 工厂：node 到位写 state（react-rnd 挂载期首帧 ref 可能仍
 * 为 null，state 通知才可靠）。 */
export function useNodeRef<T extends HTMLElement>(
  set: Dispatch<SetStateAction<T | null>>
) {
  return useCallback((node: T | null) => set(node), [set])
}
