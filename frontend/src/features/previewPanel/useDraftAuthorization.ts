/**
 * 草稿预览的逐次授权（#347 P1 / #500 P1-3/P1-5）：授权快照 {jobId,
 * workspaceId, htmlHash} 在「预览此草稿」点击时保存，render 期与当前
 * 身份/草稿内容派生比对——不给「新上下文/新内容执行旧授权」留 commit
 * 窗口。快照失效的四条路径与各自的判定形态见 isDraftAuthorized。
 */
import { useEffect, useState } from 'react'
import type { PreviewPanelVersion } from './previewPanelApi'

/** 一次「预览此草稿」点击的授权快照：当时授权的是哪个身份下的哪份草稿。 */
interface DraftAuthorization {
  jobId: string
  workspaceId: string | undefined
  htmlHash: string
}

export interface DraftAuthorizationApi {
  /** 快照与当前身份/草稿内容全等才有效（render 期派生，无 effect 窗口）。 */
  isAuthorized: boolean
  /** 授权快照已保存且尚未被收尾复位（按钮态用，非放行判定）。 */
  hasSnapshot: boolean
  /** 「预览此草稿」点击：保存当前帧的授权快照（草稿不可见时忽略）。 */
  authorize: (draft: PreviewPanelVersion) => void
}

/**
 * #500 P1-3：授权有效性是 **render 期派生**而非 effect 复位——jobId 变化
 * 的那个 commit 里 iframe 会因 key 变化重挂并以新 jobId 执行旧草稿，被动
 * effect 要等 paint 之后才复位，存在「新上下文 + 旧授权」的执行窗口；
 * 渲染时比对快照与当前身份，不一致即本次渲染就不放行，窗口消失。
 * #500 P1-5：快照绑定草稿内容（服务端 html_hash，sha256）——save_draft
 * 覆盖同一草稿（hash 变）即回退未授权，新内容需重新显式预览；同一内容
 * 的轮询刷新（hash 不变）继续自动跟随。
 */
export function useDraftAuthorization(
  jobId: string,
  workspaceId: string | undefined,
  customizing: boolean,
  draft: PreviewPanelVersion | null
): DraftAuthorizationApi {
  const [snapshot, setSnapshot] = useState<DraftAuthorization | null>(null)
  const isAuthorized =
    snapshot !== null &&
    snapshot.jobId === jobId &&
    snapshot.workspaceId === workspaceId &&
    snapshot.htmlHash === draft?.html_hash
  // render 派生覆盖不了的授权复位（#347 P1）：draft 经轮询异步 null 过渡
  // （发布/归档）与对话框关闭这两帧之后的收尾——把已不可能再派生出
  // 「授权中」的快照清掉，重开对话框回到默认态。
  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- 轮询送达的 draft null 过渡 / 对话框关闭使授权快照失效（review P1 / #500）
    if (snapshot && (!customizing || draft === null)) setSnapshot(null)
  }, [customizing, snapshot, draft])
  return {
    isAuthorized,
    hasSnapshot: snapshot !== null,
    authorize: (d: PreviewPanelVersion) =>
      setSnapshot({ jobId, workspaceId, htmlHash: d.html_hash }),
  }
}
