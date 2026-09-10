/**
 * 通用产物预览面板的可见性派生（#255 方案 A+B，拆出组件以过架构文件
 * 预算）：hidden = workspace previewHidden ∪ 结构化去重名单 − 会话内已
 * 恢复。会话态恢复是纯前端内存——去重是展示层默认而非用户偏好，不写
 * workspace 配置；普通产物的勾选仍走 workspace 配置（路由在
 * toggleVisibility）。
 */
import { useCallback, useMemo, useState } from 'react'
import { useWorkspacePreviewConfig } from './useWorkspacePreviewConfig'

export interface ArtifactVisibility {
  /** 全量隐藏名单（workspace 配置 ∪ 未恢复的去重项）。 */
  hiddenNames: ReadonlySet<string>
  /** 按隐藏名单过滤后的可见产物（保持 detail.artifacts 顺序）。 */
  visible: string[]
  /** 本次被结构化面板去重（隐藏）的产物数（摘要行「另 N 个」）。 */
  dedupedCount: number
  /** 勾选菜单回调：普通产物写 workspace 配置，去重产物走会话态恢复。 */
  toggleVisibility: (name: string, visible: boolean) => void
}

export function useArtifactVisibility(
  artifacts: readonly string[],
  workspaceId: string | undefined,
  structuredHidden: readonly string[]
): ArtifactVisibility {
  /** 会话内手动恢复展示的消费产物（折叠状态重置时回归默认，不持久化）。 */
  const [reenabled, setReenabled] = useState<ReadonlySet<string>>(
    () => new Set()
  )
  const { previewHidden, toggleArtifact } =
    useWorkspacePreviewConfig(workspaceId)

  const consumed = useMemo(() => new Set(structuredHidden), [structuredHidden])
  const dedupHidden = new Set(consumed)
  for (const name of reenabled) dedupHidden.delete(name)
  const hiddenNames = new Set([...previewHidden, ...dedupHidden])
  const visible = artifacts.filter((name) => !hiddenNames.has(name))
  const dedupedCount = artifacts.filter((name) => dedupHidden.has(name)).length

  const toggleVisibility = useCallback(
    (name: string, nextVisible: boolean) => {
      if (!consumed.has(name)) {
        void toggleArtifact(name, nextVisible)
        return
      }
      setReenabled((prev) => {
        const next = new Set(prev)
        if (nextVisible) next.add(name)
        else next.delete(name)
        return next
      })
      // 历史上被手动隐藏过的消费产物：恢复展示时同步清掉，避免两层
      // 隐藏叠加（面板隐藏 + 配置隐藏）。
      if (nextVisible && previewHidden.includes(name)) {
        void toggleArtifact(name, true)
      }
    },
    [consumed, previewHidden, toggleArtifact]
  )

  return { hiddenNames, visible, dedupedCount, toggleVisibility }
}
