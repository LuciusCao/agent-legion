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
   * 绑定解析状态（目录+定义查询的聚合，#426 review P2）：仅约束 agentId=null
   * 的「确认未绑定」——查询未 settle 时 agentId=null 只是「未知」，不渲染
   * 可操作的新建表单，避免 settle 后表单被替换丢输入、先提交重复草稿。
   * agentId 已解析（published 目录或 draft 回落命中，相关查询必有数据）时
   * 绑定目标已可信，不经此门控（#426 codex P2：另一目录在途/失败不挡
   * 编辑器，错误仍由全局横幅暴露）。
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
 * #426 review：agentId=null（待双目录确认「未绑定」）时先过绑定解析门控
 * （bindingStatus），未 settle 只给加载占位（失败给错误提示，均不落回
 * 可操作表单）；#426 codex P2：agentId 非空即绑定目标已解析，跳过门控
 * 直接渲染编辑器（其内部按该 ID 加载详情，自带加载态）。
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

  if (props.agentId === null && props.bindingStatus !== 'ready') {
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
