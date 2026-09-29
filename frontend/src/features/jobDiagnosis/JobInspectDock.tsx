/**
 * job detail 的排查 Dock（#795 PR③）：AgentPanelDock + JobDiagnosisPanel 的
 * 薄组合——页面级「排查助手」按钮与出错节点的「排查」入口唤起同一个 Dock
 * 实例（所有 agent 对话界面同一个组件、同一个载体），不再走 MUI Dialog
 * 宿主。会话语义与旧弹窗等价（#329）：打开 = 全新排查会话（自动建会话 +
 * 自动发 workspace/job/node 上下文 primer），关闭即卸载销毁本地状态；
 * 折叠为右下角小条由 Dock 承担（display:none 不卸载，会话保留）。
 * 挂载 key 由调用方按 workspace+job+node 给出：跨 workspace/job 导航或换
 * 节点重开时整棵重挂（chat 状态、composer 文本、primer 目标一律不串）。
 */
import { AgentPanelDock } from '../agentPanelDock/AgentPanelDock'
import type { JobDiagnosisTarget } from './jobDiagnosisContext'
import { JobDiagnosisPanel } from './JobDiagnosisPanel'

type Props = {
  target: JobDiagnosisTarget
  onClose: () => void
  /** 唤起瞬间的触发元素（#800 codex P2）：换节点 key 重挂时，新实例的归还
   * 目标以它为准，不吃旧实例卸载清理改写 activeElement 的交错。 */
  restoreFocusRef?: { readonly current: HTMLElement | null }
}

export function JobInspectDock({ target, onClose, restoreFocusRef }: Props) {
  const title = target.nodeLabel
    ? `排查：${target.nodeLabel}`
    : `排查：${target.jobTitle || target.jobId}`
  return (
    <AgentPanelDock
      surfaceKey="job-inspect"
      title={title}
      defaultSize={{ width: 520, height: 640 }}
      minWidth={340}
      restoreFocusRef={restoreFocusRef}
      onClose={onClose}
    >
      {/* inDock：Dock 宿主不用旧 Dialog 的 320px 底尺寸（#800 codex P2，
          否则缩到最小时 composer 被 overflow 裁掉）。 */}
      <JobDiagnosisPanel
        workspaceId={target.workspaceId}
        target={target}
        inDock
      />
    </AgentPanelDock>
  )
}
