import { useState, type ReactNode } from 'react'
import inspectorStyles from './WorkflowNodeInspector.module.css'

type Status = 'pending' | 'error' | 'ready'

type GateProps = {
  /** agentBindingStatus.bindingStatus（settle=!isFetching 语义）。 */
  status: Status
  /** render-prop：面板由 Gate 稳定承载（children 恒在同一 DOM 位点，
   * pending/error 翻转只改提示条与冻结层——条件 return 直接换树会让
   * React 卸载重挂 children，丢表单状态、重拉详情）。 */
  children: (frozen: boolean) => ReactNode
}

/**
 * #426 终局复审 P1：bindingStatus 在 ready 后翻回 pending/error（聊天
 * turn_end 失效双查询、staleTime 后聚焦重取、编辑器自身保存/发布/回滚/
 * 归档触发的 refresh——settle=!isFetching 的正确代价）时，不再卸载编辑器：
 * AgentEditor 表单是本地 useState，卸载即丢未保存输入。「首次 settle
 * 前」（从未 ready，无内容可保）渲染占位；ready 后的重取翻转保挂载：
 * 提示条提示编辑暂缓，冻结层（inert + pointer-events:none 兜底 + 降
 * 不透明度）挡住对「旧值不可信」编辑目标的操作，settle 恢复后提示
 * 消失、可继续编辑。错误态重试走 Studio 全局目录横幅
 * （WorkflowCatalogLoadError），条内不放第二入口。占位与提示条视觉沿用
 * inspectorStyles.empty 提示模式。
 * everReady 是「曾 ready」闩锁：用 setState-during-render 维持（React
 * 认可的按派生状态调整模式），比 useEffect 早一帧生效——翻转当帧就
 * 保挂载，不会先闪一帧占位。
 * 终局收尾 P3-1：冻结层加 inert——CSS pointer-events 只挡指针，键盘
 * 仍可 Tab 聚焦输入、Enter 激活按钮，读屏也无感知；inert 一次阻断
 * 指针/键盘/读屏三条路径（React 18 类型与运行时都不识 inert，按未知
 * 属性直传 DOM，布尔值会被丢弃并告警，故传 ''；19 转正后可改 boolean）。
 * 终局收尾 P3-2：闩锁是组件级的，节点 A→B 切换（capability 变化）不
 * 重置——B 的首个重取窗口也走此分支，故提示文案取中性「编辑暂缓」，
 * 不声称「输入已保留」（对从未 ready 的节点不准）。
 */
export function WorkflowNodeAgentGate(props: GateProps) {
  const [everReady, setEverReady] = useState(props.status === 'ready')
  if (props.status === 'ready' && !everReady) setEverReady(true)
  if (!everReady) {
    const pending = props.status === 'pending'
    return (
      <div
        className={inspectorStyles.empty}
        role={pending ? 'status' : 'alert'}
      >
        {pending ? 'Agent 绑定解析中...' : 'Agent 目录加载失败'}
      </div>
    )
  }
  const pending = props.status === 'pending'
  const frozen = props.status !== 'ready'
  return (
    <div aria-busy={pending}>
      {frozen ? (
        <div
          className={inspectorStyles.refetchNotice}
          role={pending ? 'status' : 'alert'}
        >
          {pending
            ? 'Agent 目录刷新中，编辑暂缓...'
            : 'Agent 目录刷新失败，重试前编辑暂缓。'}
        </div>
      ) : null}
      <div
        className={frozen ? inspectorStyles.frozen : undefined}
        {...(frozen ? ({ inert: '' } as { inert?: string }) : {})}
      >
        {props.children(frozen)}
      </div>
    </div>
  )
}
