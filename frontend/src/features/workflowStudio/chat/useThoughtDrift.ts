import { useEffect, useRef, useState } from 'react'

/** 模型切换后档位联动的漂移提示：思考档位的当前值变了、而最近一次动作不是
 * 用户自己切档 → 提示；用户切档引起的变化不提示。 */
export function useThoughtDrift(
  current: string | null,
  ownChange: boolean
): string | null {
  const previous = useRef(current)
  const [drift, setDrift] = useState<string | null>(null)
  useEffect(() => {
    if (previous.current === current) return
    setDrift(
      ownChange || current === null
        ? null
        : `思考档位已随模型切换变为 ${current}`
    )
    previous.current = current
  }, [current, ownChange])
  return drift
}
