import { useCallback, useEffect, useState } from 'react'
import { api } from '../../api'
import type { components } from '../../generated/api'
import { useSettingStore } from '../../stores/settingStore'
import { useUiStore } from '../../stores/uiStore'
import type { WorkflowNodeRecord } from '../../types'
import type { ExecutorDefinition } from '../../types/executorTypes'
import { WorkflowNodeCodeActions } from './WorkflowNodeCodeActions'
import { WorkflowNodeCodeEditor } from './WorkflowNodeCodeEditor'
import { WorkflowNodeCodePreview } from './WorkflowNodeCodePreview'
import { WorkflowNodeCodeVersions } from './WorkflowNodeCodeVersions'
import inspectorStyles from './WorkflowNodeInspector.module.css'
import styles from './WorkflowNodeCodeSection.module.css'
import {
  fetchNodeCodeTemplate,
  hasCodeCapability,
} from './workflowNodeCodeLookup'

type NodeCodeResponse = components['schemas']['WorkflowNodeCodeResponse']

function codeUrl(workspaceId: string, workflowKey: string, nodeKey: string) {
  return `/api/workspaces/${encodeURIComponent(workspaceId)}/workflows/${encodeURIComponent(workflowKey)}/nodes/${encodeURIComponent(nodeKey)}/code`
}

type LoadState = 'loading' | 'ready' | 'error'

export function WorkflowNodeCodeSection(props: {
  node: WorkflowNodeRecord
  executorCatalog: ExecutorDefinition[]
  // 与 binding editor 同一口径：visible workflow 的 key 由 Inspector 逐层
  // 下传，不取 settings 快照（草稿改 key 发布后两者会分叉）。
  workflowKey: string
  readOnly?: boolean
}) {
  const workspaceId = useSettingStore((s) => s.workspaceId)
  const codeBound = hasCodeCapability(
    props.executorCatalog,
    props.node.capability
  )

  const [loadState, setLoadState] = useState<LoadState>('loading')
  const [data, setData] = useState<NodeCodeResponse | null>(null)
  const [error, setError] = useState('')
  const [editing, setEditing] = useState(false)
  const [busy, setBusy] = useState(false)
  const [showVersions, setShowVersions] = useState(false)
  const [versionsToken, setVersionsToken] = useState(0)
  const [confirmingReset, setConfirmingReset] = useState(false)

  const url =
    workspaceId && props.workflowKey
      ? codeUrl(workspaceId, props.workflowKey, props.node.key)
      : null

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
  const run = async (action: () => Promise<unknown>, success: string) => {
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
      setError(err instanceof Error ? err.message : '操作失败')
    } finally {
      setBusy(false)
    }
  }

  const putDraft = (code: string, changeNote: string | null = null) =>
    api(url, {
      method: 'PUT',
      body: JSON.stringify({ code, change_note: changeNote }),
    })
  const saveDraft = (code: string, changeNote: string) =>
    run(() => putDraft(code, changeNote || null), '草稿已保存')
  const createFromTemplate = () =>
    run(
      async () => putDraft((await fetchNodeCodeTemplate()).code),
      '已从模板创建草稿'
    )
  const publish = () =>
    run(
      () => api(`${url}/publish`, { method: 'POST' }),
      '已发布，新执行立即生效'
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
