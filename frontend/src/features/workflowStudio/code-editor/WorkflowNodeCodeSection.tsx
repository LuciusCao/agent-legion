import { useCallback, useEffect, useState } from 'react'
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
type NodeCodeVersionResponse = components['schemas']['WorkflowNodeCodeVersionResponse']

// #749：发布 CAS（expected_hash）被服务端拒绝的专用文案——草稿在加载后被
// 其他会话/编辑器覆盖。与聊天草稿卡的 409 文案同一交互模式：内联提示 +
// 引导重新加载，不发明新 UI。
const DRAFT_OVERRIDDEN_HINT = '草稿已被其他会话或编辑器更新，请重新加载后再保存发布'

// #749：详情 GET 不带草稿 hash 的兜底提示（版本偏斜：旧后端不回
// draft_code_hash）——对齐 EntityDraftPublishButton 的 null-hash 立场：
// 无 CAS 令牌的发布会退回无核对语义，静默发出可能已被覆盖的旧草稿。
const NO_DRAFT_HASH_HINT = '草稿缺少可核对的版本标识（后端版本偏斜），请升级后端再发布'

const statusOf = (err: unknown) =>
  (err as { status?: number } | null)?.status

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

  // WorkflowNodeCodeSection is keyed by node in the inspector, so this effect
  // only runs on mount (and after explicit reloads via its own calls).
  const reload = useCallback(() => {
    if (!url || !codeBound) return undefined
    let cancelled = false
    api<NodeCodeResponse>(url)
      .then((result) => {
        if (cancelled) return
        setData(result)
        setLoadState('ready')
      })
      .catch((err: unknown) => {
        if (cancelled) return
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
  // 404（start node 拒绝、版本不存在）仍直显后端 detail。
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
