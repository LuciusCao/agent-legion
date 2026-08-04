import { useEffect, useState } from 'react'
import { api } from '../../api'
import type { components } from '../../generated/api'
import { useAuthStore } from '../../stores/authStore'
import type { WorkflowNodeRecord } from '../../types'
import type { ExecutorDefinition } from '../../types/executorTypes'
import { findCapabilityBindings } from './WorkflowExecutorBindingList'
import inspectorStyles from './WorkflowNodeInspector.module.css'
import styles from './WorkflowNodeCodeSection.module.css'

type NodeFileResponse = components['schemas']['WorkflowNodeFileResponse']
type CapabilityReference =
  components['schemas']['WorkflowNodeCapabilityReference']

// A node has a viewable code file only when its capability is bound to a
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

function NodeCodeViewer(props: { path: string }) {
  const [loadState, setLoadState] = useState<LoadState>('loading')
  const [content, setContent] = useState('')
  const [references, setReferences] = useState<CapabilityReference[]>([])
  const [error, setError] = useState('')

  // NodeCodeViewer is keyed by path, so the effect only runs on mount; the
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
      <pre className={styles.code}>{content}</pre>
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
        内置节点代码只读；代码变更经 git review + CI 入库后生效。
        {props.readOnly && '当前为历史版本查看模式，节点代码不属于 revision。'}
      </div>
      <NodeCodeViewer key={codePath} path={codePath} />
    </section>
  )
}
