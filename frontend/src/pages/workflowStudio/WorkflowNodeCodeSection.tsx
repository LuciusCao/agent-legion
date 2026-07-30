import { useEffect, useState } from 'react'
import { Button } from '@mui/material'
import { api } from '../../api'
import type { components } from '../../generated/api'
import { useAuthStore } from '../../stores/authStore'
import { useUiStore } from '../../stores/uiStore'
import type { WorkflowNodeRecord } from '../../types'
import type { ExecutorDefinition } from '../../types/executorTypes'
import { findCapabilityBindings } from './WorkflowExecutorBindingList'
import inspectorStyles from './WorkflowNodeInspector.module.css'
import styles from './WorkflowNodeCodeSection.module.css'

type NodeFileResponse = components['schemas']['WorkflowNodeFileResponse']
type NodeFileUpdateResponse =
  components['schemas']['WorkflowNodeFileUpdateResponse']
type CapabilityReference =
  components['schemas']['WorkflowNodeCapabilityReference']

// A node has an editable code file only when its capability is bound to a
// kind="code" executor whose capability detail declares a module path.
export function findNodeCodePath(
  executorCatalog: ExecutorDefinition[],
  capability: string
): string | null {
  const binding = findCapabilityBindings(executorCatalog, capability).find(
    ({ executor, detail }) => executor.kind === 'code' && Boolean(detail.path)
  )
  return binding?.detail.path ?? null
}

function fileUrl(path: string) {
  return `/api/workflow-nodes/files/${path
    .split('/')
    .map(encodeURIComponent)
    .join('/')}`
}

function formatReferences(references: CapabilityReference[]) {
  return references
    .map((ref) => `${ref.executor_id}: ${ref.capability}`)
    .join(', ')
}

type LoadState = 'loading' | 'ready' | 'error'

function NodeCodeEditor(props: { path: string }) {
  const [loadState, setLoadState] = useState<LoadState>('loading')
  const [content, setContent] = useState('')
  const [draft, setDraft] = useState('')
  const [references, setReferences] = useState<CapabilityReference[]>([])
  const [editing, setEditing] = useState(false)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')

  // NodeCodeEditor is keyed by path, so the effect only runs on mount; the
  // initial state already covers the loading branch.
  useEffect(() => {
    let cancelled = false
    api<NodeFileResponse>(fileUrl(props.path))
      .then((result) => {
        if (cancelled) return
        setContent(result.content)
        setReferences(result.capabilities ?? [])
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
  }, [props.path])

  const save = async () => {
    setSaving(true)
    setError('')
    try {
      const result = await api<NodeFileUpdateResponse>(fileUrl(props.path), {
        method: 'PUT',
        body: JSON.stringify({ content: draft }),
      })
      setContent(draft)
      setReferences(result.capabilities ?? [])
      setEditing(false)
      useUiStore.getState().showToast('节点代码已保存，下次执行生效', 'success')
    } catch (err) {
      setError(err instanceof Error ? err.message : '保存失败')
    } finally {
      setSaving(false)
    }
  }

  if (loadState === 'loading') {
    return <div className={styles.hint}>加载代码中...</div>
  }
  if (loadState === 'error') {
    return (
      <div role="alert" className={styles.error}>
        {error}
      </div>
    )
  }
  return (
    <>
      {references.length > 0 && (
        <div className={styles.hint}>
          引用 capability:{formatReferences(references)}
        </div>
      )}
      {editing ? (
        <>
          <textarea
            aria-label="节点代码内容"
            className={styles.editor}
            value={draft}
            onChange={(event) => setDraft(event.target.value)}
            disabled={saving}
          />
          <div className={styles.actions}>
            <Button
              variant="outlined"
              size="small"
              onClick={() => void save()}
              disabled={saving}
            >
              {saving ? '保存中...' : '保存'}
            </Button>
            <Button
              variant="text"
              size="small"
              onClick={() => {
                setEditing(false)
                setError('')
              }}
              disabled={saving}
            >
              取消
            </Button>
          </div>
        </>
      ) : (
        <>
          <pre className={styles.code}>{content}</pre>
          <div className={styles.actions}>
            <Button
              variant="outlined"
              size="small"
              onClick={() => {
                setDraft(content)
                setEditing(true)
              }}
            >
              编辑
            </Button>
          </div>
        </>
      )}
      {error && (
        <div role="alert" className={styles.error}>
          {error}
        </div>
      )}
    </>
  )
}

export function WorkflowNodeCodeSection(props: {
  node: WorkflowNodeRecord
  executorCatalog: ExecutorDefinition[]
  readOnly?: boolean
}) {
  // The node-file endpoints are admin-only; non-admin users would only see a
  // 403, so the whole card is hidden for them (same pattern as SettingsPage).
  const isAdmin = useAuthStore((s) => s.user?.role === 'admin')
  const codePath = findNodeCodePath(
    props.executorCatalog,
    props.node.capability
  )
  if (!isAdmin || !codePath) return null
  return (
    <section className={inspectorStyles.section} aria-label="节点代码">
      <div className={inspectorStyles.sectionTitle}>节点代码</div>
      <div className={styles.path}>{codePath}</div>
      <div className={styles.hint}>
        代码保存后立即生效，不随工作流草稿/发布流程。
        {props.readOnly &&
          '当前为历史版本查看模式，节点代码不属于 revision，仍可编辑。'}
      </div>
      <NodeCodeEditor key={codePath} path={codePath} />
    </section>
  )
}
