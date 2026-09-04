import { useQueryClient } from '@tanstack/react-query'
import { useSettingStore } from '../../../stores/settingStore'
import { extraQueryKeys } from '../../../lib/queryKeysExtra'
import { WorkflowNodeAgentEditorPanel } from './WorkflowNodeAgentEditorPanel'
import { WorkflowNodeAgentGate } from './WorkflowNodeAgentGate'
import type { AgentBindingStatus } from './agentBindingStatus'

type Props = {
  /** 已绑定该 capability 的 Agent id；null = 新建模式（capability 预填）。 */
  agentId: string | null
  capability: string
  readOnly?: boolean
  /**
   * 绑定解析门控（#426 review P2 → codex 终轮 P2，节点级按 capability
   * 计算，见 agentBindingStatus.bindingStatus，调用方为
   * WorkflowNodeExecutionSection）：pending=目录在途、或未命中 published
   * 且 definitions 在途（agentId 可能只是 draft 回落先行，settle 后同
   * capability 的 published Agent 会替换它；或 agentId=null 只是「未知」
   * 而非「未绑定」）→ 首次出加载占位；error=catalog 失败且无数据、或未
   * 命中 published 且 definitions 失败无数据 → 错误占位；ready=「published
   * ?? draft」已是终态，放行渲染编辑器/新建表单。
   */
  bindingStatus: AgentBindingStatus
}

/**
 * type=agent 节点详情内嵌的 Agent 编辑/新建区（#392 起只挂在 agent 节点
 * 上；code 节点的类型变更走头部类型选择器）。Agent 定义仍是 workspace 级
 * 共享实体（versioned_entities，一 capability 一 published），此处仅改变
 * UI 承载；保存/发布/归档后失效 Agent 目录与 Studio capability 路由缓存。
 * #409：去掉「编辑 Agent」开合按钮——Agent 区块直接内联展开编辑面板，
 * 只读/无 workspace 时整块隐藏。
 * #426 review P2：目录未 settle 时 agentId=null 只是「未知」而非「未
 * 绑定」，不出可操作的新建表单；#426 codex P2 修正：门控以 published
 * 目录 settle 为准（bindingStatus），与 agentId 是否已解析无关——目录在
 * 途时 draft 回落的非空 agentId 同样等待（settle 后可能被同 capability
 * 的 published Agent 替换，先放行会丢输入/撞发布冲突）；目录已返回
 * （ready）则「published ?? draft」为终态，编辑目标不再漂移，直接放行。
 * codex 终轮 P2：catalog 空列表/未命中 published 时 ready 还需 definitions
 * settle（未命中时 useCapabilityAgent 的空 draft 列表会误判未绑定，
 * definitions 返回后切换真实 draft 重挂丢输入），计算见
 * agentBindingStatus.bindingStatus。
 * #426 终局复审 P1：settle=!isFetching（第四轮正确性）使 ready→pending
 * 翻转发生在每次失效重取（聊天 turn_end 的双查询失效、staleTime 30s 后
 * 的聚焦重取）——翻转即卸载会丢未保存输入，故 ready 后的重取期改由
 * WorkflowNodeAgentGate 保挂载 + 冻结遮罩（输入保留，settle 后恢复可
 * 编辑），仅首次 settle 前渲染占位；P3-2：编辑器自身保存/发布/回滚触发
 * 的 refresh 同属此路径，不再卸载闪烁/重复拉详情。
 */
export function WorkflowNodeAgentEditor(props: Props) {
  const queryClient = useQueryClient()
  const workspaceId = useSettingStore((s) => s.workspaceId) ?? undefined
  if (props.readOnly || !workspaceId) return null

  function refresh() {
    void queryClient.invalidateQueries({
      queryKey: extraQueryKeys.agentDefinitions(workspaceId ?? ''),
    })
    // Agent 发布/归档/回滚改变 capability 路由，Studio 目录同会话失效重取。
    void queryClient.invalidateQueries({
      queryKey: extraQueryKeys.studioAgentCatalog(workspaceId ?? ''),
    })
  }

  return (
    <WorkflowNodeAgentGate status={props.bindingStatus}>
      {() => (
        <WorkflowNodeAgentEditorPanel
          key={props.capability}
          workspaceId={workspaceId}
          agentId={props.agentId}
          capability={props.capability}
          onRefresh={refresh}
        />
      )}
    </WorkflowNodeAgentGate>
  )
}
