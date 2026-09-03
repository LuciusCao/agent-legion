import { useQueryClient } from '@tanstack/react-query'
import { useSettingStore } from '../../../stores/settingStore'
import { extraQueryKeys } from '../../../lib/queryKeysExtra'
import { WorkflowNodeAgentEditorPanel } from './WorkflowNodeAgentEditorPanel'
import inspectorStyles from './WorkflowNodeInspector.module.css'

type Props = {
  /** 已绑定该 capability 的 Agent id；null = 新建模式（capability 预填）。 */
  agentId: string | null
  capability: string
  readOnly?: boolean
  /**
   * 目录 settle 状态（published catalog 查询，#426 review P2→codex P2）：
   * 门控条件。pending=目录在途（agentId 可能只是 draft 回落先行——
   * definitions 先返回时 useCapabilityAgent 已给出非空 agentId，但目录
   * settle 后同 capability 的 published Agent 会替换它，编辑目标会漂移、
   * 输入会丢）→ 只渲染加载占位；error=目录失败且无数据 → 错误占位；
   * ready=目录已返回，「published ?? draft」是终态——agentId 非空
   * （published 命中或 draft 回落）或 null（确认无 published）都不会再
   * 变，放行渲染编辑器/新建表单。
   */
  bindingStatus: 'pending' | 'error' | 'ready'
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

  if (props.bindingStatus !== 'ready') {
    const pending = props.bindingStatus === 'pending'
    return (
      <div
        className={inspectorStyles.empty}
        role={pending ? 'status' : 'alert'}
      >
        {pending ? 'Agent 绑定解析中...' : 'Agent 目录加载失败'}
      </div>
    )
  }

  return (
    <WorkflowNodeAgentEditorPanel
      key={props.capability}
      workspaceId={workspaceId}
      agentId={props.agentId}
      capability={props.capability}
      onRefresh={refresh}
    />
  )
}
