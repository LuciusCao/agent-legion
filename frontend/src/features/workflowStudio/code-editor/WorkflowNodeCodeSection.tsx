import { useCallback, useEffect, useRef, useState } from 'react'
import { api } from '../../../api'
import type { components } from '../../../generated/api'
import { useSettingStore } from '../../../stores/settingStore'
import { useUiStore } from '../../../stores/uiStore'
import type { WorkflowNodeRecord } from '../../../types'
import { WorkflowNodeCodeActions } from './WorkflowNodeCodeActions'
import { WorkflowNodeCodeEditor } from './WorkflowNodeCodeEditor'
import { WorkflowNodeCodePreview } from './WorkflowNodeCodePreview'
import { WorkflowNodeCodeVersions } from './WorkflowNodeCodeVersions'
import inspectorStyles from '../inspector/WorkflowNodeInspector.module.css'
import styles from './WorkflowNodeCodeSection.module.css'
import { fetchNodeCodeTemplate } from './workflowNodeCodeLookup'

type NodeCodeResponse = components['schemas']['WorkflowNodeCodeResponse']
type NodeCodeVersionResponse =
  components['schemas']['WorkflowNodeCodeVersionResponse']

// #749：发布 CAS（expected_hash）被服务端拒绝的专用文案——草稿在加载后被
// 其他会话/编辑器覆盖。与聊天草稿卡的 409 文案同一交互模式：内联提示 +
// 引导重新加载，不发明新 UI。
const DRAFT_OVERRIDDEN_HINT =
  '草稿已被其他会话或编辑器更新，请重新加载后再保存发布'

// #749：详情 GET 不带草稿 hash 的兜底提示（版本偏斜：旧后端不回
// draft_code_hash）——对齐 EntityDraftPublishButton 的 null-hash 立场：
// 无 CAS 令牌的发布会退回无核对语义，静默发出可能已被覆盖的旧草稿。
const NO_DRAFT_HASH_HINT =
  '草稿缺少可核对的版本标识（后端版本偏斜），请升级后端再发布'

const statusOf = (err: unknown) => (err as { status?: number } | null)?.status

function codeUrl(workspaceId: string, nodeKey: string) {
  // workflows/{workflowKey} 路径段已退役（#211）：key 与 workspace id 自
  // schema v62 起恒等，节点代码路由改挂 workspace 下。
  return `/api/workspaces/${encodeURIComponent(workspaceId)}/nodes/${encodeURIComponent(nodeKey)}/code`
}

type LoadState = 'loading' | 'ready' | 'error'

export function WorkflowNodeCodeSection(props: {
  node: WorkflowNodeRecord
  readOnly?: boolean
}) {
  const workspaceId = useSettingStore((s) => s.workspaceId)
  // 防御门控（nodeTypeSections 注册表之外的独立保证）：节点代码是
  // code 池专属，非 code 类型一律不渲染——组件被直接渲染时也不得
  // 对 agent/approval 节点发节点代码请求。
  const codeBound = (props.node.node_type ?? 'code') === 'code'

  const [loadState, setLoadState] = useState<LoadState>('loading')
  const [data, setData] = useState<NodeCodeResponse | null>(null)
  const [error, setError] = useState('')
  const [editing, setEditing] = useState(false)
  const [busy, setBusy] = useState(false)
  const [showVersions, setShowVersions] = useState(false)
  const [versionsToken, setVersionsToken] = useState(0)
  const [confirmingReset, setConfirmingReset] = useState(false)

  const url = workspaceId ? codeUrl(workspaceId, props.node.key) : null

  // #749 修（codex #756 P2）：reload 请求代次。每次发起 reload 递增并捕获
  // 当次序号；响应到达时序号已非最新 ⇒ 该请求发起于更早的状态（如上一次
  // 保存之前），整体丢弃——无论其内容看起来多新，它代表的快照已经过时，
  // 最新状态由后续 reload 或保存回填负责。版本比较识别不了这种滞留：连续
  // 保存同一份已有草稿时 save_draft 原地更新草稿行、draft_version 不递增，
  // 旧响应版本相等，`<` 判 false，会把保存回填的新 hash 打回旧值，发布带
  // 旧 expected_hash 稳定 409。「他端更新」不受误杀：那种场景本会话最后
  // 发出的 reload 就是最新代次，只有被更新的 reload 覆盖的旧请求才丢。
  const reloadGenerationRef = useRef(0)

  // WorkflowNodeCodeSection is keyed by node in the inspector, so this effect
  // only runs on mount (and after explicit reloads via its own calls).
  const reload = useCallback(() => {
    if (!url || !codeBound) return undefined
    const generation = ++reloadGenerationRef.current
    let cancelled = false
    api<NodeCodeResponse>(url)
      .then((result) => {
        // cancelled：组件卸载 / url 变更（effect cleanup）；generation：该
        // 响应已被更新的 reload 取代，滞留快照整体作废（codex #756）。
        if (cancelled || generation !== reloadGenerationRef.current) return
        // #749 修（R2 P3，降级为次级防御）：代次只保证「这是最新发出的
        // 请求」，不保证其快照不旧——读侧可能滞后（副本延迟/池化连接的
        // 旧快照），最新 reload 仍可能回 pre-save 形态。函数式合并按
        // draft_version 保留较新一侧的草稿身份（hash / version / code
        // 同属一个保存，一起保留），其余字段以 reload 为准（后台刷新的
        // 本意）；响应侧无草稿（已发布/已回落）或不比本地新时原样采纳。
        setData((prev) =>
          prev?.draft_code_hash &&
          result.has_draft &&
          (result.draft_version ?? 0) < (prev.draft_version ?? 0)
            ? {
                ...result,
                draft_code: prev.draft_code,
                draft_version: prev.draft_version,
                draft_code_hash: prev.draft_code_hash,
              }
            : result
        )
        setLoadState('ready')
      })
      .catch((err: unknown) => {
        if (cancelled || generation !== reloadGenerationRef.current) return
        setError(err instanceof Error ? err.message : '加载失败')
        setLoadState('error')
      })
    return () => {
      cancelled = true
    }
  }, [url, codeBound])
  useEffect(() => reload(), [reload])

  if (!url || !codeBound) return null

  const toast = useUiStore.getState().showToast
  // #749：错误展示区分 CAS 拒绝（409）——保存→发布之间草稿被其他会话/
  // 编辑器覆盖，正是 expected_hash 要抓的竞态；专用文案引导重新加载。
  const errorFor = (err: unknown) =>
    statusOf(err) === 409
      ? DRAFT_OVERRIDDEN_HINT
      : err instanceof Error
        ? err.message
        : '操作失败'
  // #749 修：404 分支只挂发布路径——无草稿可发（刚在别处发布过），与
  // 聊天草稿卡同款可行动文案（EntityDraftPublishButton）；保存/回滚的
  // 404（版本不存在）仍直显后端 detail。
  // （start node 拒绝同样走 404 且同样命中发布路径——publish_node_code
  // 也先跑 _reject_start_node——该形态下此文案不精确，但发布仍被拦；
  // 低概率路径，不为它拆分支，对齐 EntityDraftPublishButton 的明文承认。）
  const publishErrorFor = (err: unknown) =>
    statusOf(err) === 404 ? '没有待发布的草稿（可能刚已发布过）' : errorFor(err)
  const run = async (
    action: () => Promise<unknown>,
    success: string,
    errorForFn: (err: unknown) => string = errorFor
  ) => {
    setBusy(true)
    setError('')
    try {
      await action()
      toast(success, 'success')
      setEditing(false)
      setConfirmingReset(false)
      setVersionsToken((token) => token + 1)
      reload()
    } catch (err) {
      setError(errorForFn(err))
    } finally {
      setBusy(false)
    }
  }

  const putDraft = (code: string, changeNote: string | null = null) =>
    api<NodeCodeVersionResponse>(url, {
      method: 'PUT',
      body: JSON.stringify({ code, change_note: changeNote }),
    })
  // #749 修：保存/从模板创建后同步回填草稿 hash——PUT 响应（code_hash）
  // 就是刚写入的草稿身份，发布闭包立即拿到新令牌，消灭「保存→立即发布
  // 拿旧 hash 撞假 409」的窗口（对齐 AgentEditor.handleSaveDraft 的
  // saved.definition_hash 同步回填；reload 仍后台刷新其余字段）。
  const runSavingDraft = (
    action: () => Promise<NodeCodeVersionResponse>,
    success: string
  ) =>
    run(async () => {
      const saved = await action()
      setData((prev) =>
        prev
          ? {
              ...prev,
              has_draft: true,
              draft_code: saved.code,
              draft_version: saved.version,
              draft_code_hash: saved.code_hash,
            }
          : prev
      )
    }, success)
  const saveDraft = (code: string, changeNote: string) =>
    runSavingDraft(() => putDraft(code, changeNote || null), '草稿已保存')
  const createFromTemplate = () =>
    runSavingDraft(
      async () => putDraft((await fetchNodeCodeTemplate()).code),
      '已从模板创建草稿'
    )
  // #749：发布带 expected_hash（详情读取的 draft_code_hash，或自己保存
  // 草稿时保存响应同步回填的新 hash），服务端在发布事务内 CAS 核对，
  // 不匹配 409 零副作用。无 hash 的口子改由按钮 disabled 封死（见
  // publishDisabled），不走到可点的 reject。
  const publish = () =>
    run(
      () =>
        api(`${url}/publish`, {
          method: 'POST',
          body: JSON.stringify({ expected_hash: data?.draft_code_hash }),
        }),
      '已发布，新执行立即生效',
      publishErrorFor
    )
  const rollback = (version: number) =>
    run(
      () =>
        api(`${url}/rollback`, {
          method: 'POST',
          body: JSON.stringify({ version }),
        }),
      `已回滚到 v${version} 的代码（新版本）`
    )
  const resetToBuiltin = () =>
    run(() => api(url, { method: 'DELETE' }), '已回落到内置实现')

  const writable = !props.readOnly
  const isCustom = data?.origin === 'custom'

  return (
    <section className={inspectorStyles.section} aria-label="节点代码">
      <div className={inspectorStyles.sectionTitle}>节点代码</div>
      <div className={styles.path}>
        {isCustom
          ? `自定义 v${data?.version}`
          : data?.origin === 'builtin'
            ? '出厂版本（全局种子）'
            : '无代码版本'}
        {data?.has_draft && <span className={styles.badge}>有未发布草稿</span>}
      </div>
      {props.readOnly && (
        <div className={styles.hint}>
          当前为历史版本查看模式，节点代码不属于 revision。
        </div>
      )}
      {loadState === 'loading' && (
        <div className={styles.hint}>加载代码中...</div>
      )}
      {loadState === 'error' && (
        <div role="alert" className={styles.error}>
          {error}
        </div>
      )}
      {loadState === 'ready' && data && (
        <>
          {editing ? (
            <WorkflowNodeCodeEditor
              // An unpublished draft wins over the effective code, so
              // re-editing never clobbers it blindly.
              initialCode={data.draft_code ?? data.code}
              busy={busy}
              maxCodeBytes={data.max_code_bytes}
              onSave={(code, note) => void saveDraft(code, note)}
              onCancel={() => setEditing(false)}
            />
          ) : (
            <WorkflowNodeCodePreview nodeKey={props.node.key} data={data} />
          )}
          {error && (
            <div role="alert" className={styles.error}>
              {error}
            </div>
          )}
          {writable && !editing && (
            <WorkflowNodeCodeActions
              isCustom={isCustom}
              hasBuiltin={data?.origin === 'builtin'}
              hasDraft={data.has_draft}
              busy={busy}
              // #749：有草稿但拿不到 hash（旧后端 GET 不回 draft_code_hash）
              // 时禁用发布并给 tooltip 说明——无 CAS 令牌的发布会退回无
              // 核对语义（对齐 EntityDraftPublishButton 的 null-hash 立场）。
              publishDisabled={Boolean(data.has_draft) && !data.draft_code_hash}
              publishDisabledReason={NO_DRAFT_HASH_HINT}
              confirmingReset={confirmingReset}
              onEdit={() => setEditing(true)}
              onCreateFromTemplate={() => void createFromTemplate()}
              onPublish={() => void publish()}
              onToggleVersions={() => setShowVersions((value) => !value)}
              onRequestReset={() => setConfirmingReset(true)}
              onCancelReset={() => setConfirmingReset(false)}
              onConfirmReset={() => void resetToBuiltin()}
            />
          )}
          {showVersions && (
            <WorkflowNodeCodeVersions
              key={versionsToken}
              url={`${url}/versions`}
              onRollback={(version) => void rollback(version)}
              disabled={busy || !writable}
            />
          )}
        </>
      )}
    </section>
  )
}
