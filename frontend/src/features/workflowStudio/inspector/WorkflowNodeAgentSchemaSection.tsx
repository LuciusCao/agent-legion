import { useQuery } from '@tanstack/react-query'
import { useParams } from 'react-router-dom'
import { fetchAgentDefinition } from '../../../api/agentDefinitions'
import { extraQueryKeys } from '../../../lib/queryKeysExtra'
import type {
  AgentDetailResponse,
  ConfigSchema,
  ConfigSchemaProperty,
} from '../../../types'
import { bindingStatus } from './agentBindingStatus'
import inspectorStyles from './WorkflowNodeInspector.module.css'
import type { InspectorSectionProps } from './WorkflowNodeInspectorSections'
import { useCapabilityAgent } from './useAgentDefinitions'
import styles from './WorkflowStructuredEditor.module.css'

type Props = Pick<
  InspectorSectionProps,
  'details' | 'agentCatalog' | 'agentCatalogSettle' | 'readOnly'
>

// Agent 定义详情的查询 key 必须是 agentDefinitions 列表 key 的子级：内嵌
// Agent 编辑器保存/发布/归档、聊天 turn_end、Agent 发布请求轮询的失效都
// 按列表 key 前缀整体命中，生效 schema 随之刷新——不新增第二条失效路径。
const agentDefinitionDetailKey = (ws: string, agentId: string) =>
  [...extraQueryKeys.agentDefinitions(ws), agentId] as const

// Agent 定义详情 → 生效 config_schema。生成类型把 definition 暴露为
// unknown blob，客户端按 ConfigSchema 子集解释（与 types/index.ts 同
// 约定）。dispatch 只解析 published 版本，故 published 优先；draft-only
// Agent（#387 回落）无 published，展示 latest 草稿并由 isDraft 提示
// 「发布后生效」。
function effectiveAgentSchema(
  detail: AgentDetailResponse | undefined
): ConfigSchema | undefined {
  const definition = (detail?.published ?? detail?.latest)?.definition as
    | Record<string, unknown>
    | undefined
  const schema = definition?.config_schema
  return typeof schema === 'object' && schema !== null
    ? (schema as ConfigSchema)
    : undefined
}

// 只读行的单参数摘要：类型 + 默认值 + 运行开关/敏感标记（词表与 code
// 节点 schema 编辑区一致）。
function propertySummary(prop: ConfigSchemaProperty): string {
  const parts: string[] = [prop.type]
  if (prop.default !== undefined) parts.push(`默认 ${String(prop.default)}`)
  if (prop.runtime_mutable) parts.push('运行开关')
  if (prop.secret) parts.push('敏感')
  return parts.join(' · ')
}

// agent 节点的「配置 Schema」区块（#406）：生效 schema 的权威来源是
// Agent 定义，节点 YAML 的 config_schema 不参与解析——区块只读展示，
// 编辑入口在上方「Agent 配置」的内嵌 Agent 编辑器。绑定解析与门控和
// WorkflowNodeExecutionSection 同源（useCapabilityAgent + bindingStatus）。
export function WorkflowNodeAgentSchemaSection(props: Props) {
  const node = props.details.node
  const { agent, isDraft } = useCapabilityAgent({
    node,
    agentCatalog: props.agentCatalog,
  })
  const status = bindingStatus({ agent, isDraft }, props.agentCatalogSettle)
  const { workspaceId } = useParams<{ workspaceId: string }>()
  const agentId = agent?.id
  const detail = useQuery({
    queryKey: agentDefinitionDetailKey(workspaceId ?? '', agentId ?? ''),
    queryFn: () => fetchAgentDefinition(workspaceId!, agentId!),
    // 绑定未 ready 时不发详情请求：settle 后 published 命中可能替换
    // draft 回落，先拉会闪旧数据。
    enabled: status === 'ready' && Boolean(workspaceId && agentId),
  })
  // 防御：注册表只把本区块挂在 agent 类型上；直接喂其他类型渲染空。
  // hooks 在早退前调用。
  if (node.node_type !== 'agent') return null
  const properties = effectiveAgentSchema(detail.data)?.properties ?? {}
  const keys = Object.keys(properties).filter(
    (key) => properties[key] != null && typeof properties[key] === 'object'
  )
  return (
    <section
      className={inspectorStyles.section}
      aria-label={`配置 Schema ${node.key}`}
    >
      <div className={inspectorStyles.sectionTitle}>配置 Schema</div>
      <p className={styles.fieldHint}>
        可调参数由 Agent 定义维护，这里只读展示当前生效内容；Agent 发布
        新版本后此处随之更新。
        {!props.readOnly
          ? ' 如需调整，请在上方「Agent 配置」区块编辑并发布 Agent。'
          : ''}
      </p>
      {status === 'pending' ? (
        <div className={inspectorStyles.empty} role="status">
          生效配置参数解析中...
        </div>
      ) : status === 'error' ? (
        <div className={inspectorStyles.empty} role="alert">
          Agent 目录加载失败，暂无法展示配置参数。
        </div>
      ) : !agent ? (
        <div className={inspectorStyles.empty}>
          该 capability 尚无 Agent，暂无生效配置参数。
        </div>
      ) : detail.isPending ? (
        <div className={inspectorStyles.empty} role="status">
          生效配置参数加载中...
        </div>
      ) : detail.isError ? (
        <div className={inspectorStyles.empty} role="alert">
          Agent 定义加载失败，暂无法展示配置参数。
        </div>
      ) : keys.length === 0 ? (
        <div className={inspectorStyles.empty}>该 Agent 未声明可调参数。</div>
      ) : (
        <>
          {isDraft && (
            <p className={styles.fieldHint}>
              当前展示的是草稿内容，发布后才会生效。
            </p>
          )}
          <div className={styles.fieldStack}>
            {keys.map((key) => {
              const prop = properties[key]!
              return (
                <div key={key} className={styles.fieldGroup}>
                  <div className={inspectorStyles.listItem}>
                    {`${key} · ${propertySummary(prop)}`}
                  </div>
                  {prop.description && (
                    <p className={styles.fieldHint}>{prop.description}</p>
                  )}
                </div>
              )
            })}
          </div>
        </>
      )}
    </section>
  )
}
