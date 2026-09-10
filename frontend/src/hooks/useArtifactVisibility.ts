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
  /** 手动恢复展示的消费产物（挂载内会话态：折叠/展开不重置、切换任务
   * 经面板 key 重挂载即回归默认；不持久化——去重是展示层默认而非用户
   * 偏好）。 */
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
  // 审核 P3：摘要计数只数「因去重而隐藏」的——同时被用户配置隐藏的不
  // 算「已在上方展示」（那是用户自己的选择，不是结构化面板消费）。
  const dedupedCount = artifacts.filter(
    (name) => dedupHidden.has(name) && !previewHidden.includes(name)
  ).length

  const toggleVisibility = useCallback(
    (name: string, nextVisible: boolean) => {
      if (!consumed.has(name)) {
        void toggleArtifact(name, nextVisible)
        return
      }
      // codex P2：消费产物的会话态恢复不得触碰 workspace 配置——用户
      // 在设置页/旧版菜单写入的 previewHidden 是跨任务/跨用户的持久偏
      // 好，会话级「临时看一眼」就把它永久删掉与「恢复仅为会话态」的
      // 契约相反。两层隐藏在会话内叠加即可（dedupHidden 与 previewHidden
      // 的并集天然实现），刷新/切换任务后回归持久偏好。
      setReenabled((prev) => {
        const next = new Set(prev)
        if (nextVisible) next.add(name)
        else next.delete(name)
        return next
      })
    },
    [consumed]
  )

  return { hiddenNames, visible, dedupedCount, toggleVisibility }
}
