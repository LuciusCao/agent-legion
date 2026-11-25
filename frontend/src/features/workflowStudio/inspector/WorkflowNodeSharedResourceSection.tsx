import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { listSharedResources } from '../../../api/sharedResources'
import { sharedResourceKeys } from '../../sharedResources/sharedResourceQueryKeys'
import { useSettingStore } from '../../../stores/settingStore'
import type { WorkflowNodeRecord } from '../../../types'
import {
  parseWorkflowNode,
  patchWorkflowNodeSharedResources,
} from '../shared/workflowStudioYamlDraft'
import { normalizeStringList } from '../shared/workflowStudioYamlDraft.parse'
import inspectorStyles from './WorkflowNodeInspector.module.css'
import styles from './WorkflowNodeSharedResourceSection.module.css'

export function WorkflowNodeSharedResourceSection(props: {
  node: WorkflowNodeRecord
  definitionYaml: string
  setDefinitionYaml: (value: string) => void
  readOnly?: boolean
}) {
  const workspaceId = useSettingStore((state) => state.workspaceId)
  const [patchError, setPatchError] = useState('')
  const resourcesQuery = useQuery({
    queryKey: sharedResourceKeys.list(workspaceId ?? ''),
    queryFn: ({ signal }) => listSharedResources(workspaceId!, signal),
    enabled: Boolean(workspaceId),
  })
  const draft = parseWorkflowNode(props.definitionYaml, props.node.key)
  const selected = normalizeStringList(draft?.shared_resources)
  const resources = resourcesQuery.data?.resources ?? []

  const toggle = (resourceId: string, checked: boolean) => {
    setPatchError('')
    const next = checked
      ? [...selected.filter((id) => id !== resourceId), resourceId]
      : selected.filter((id) => id !== resourceId)
    try {
      props.setDefinitionYaml(
        patchWorkflowNodeSharedResources(
          props.definitionYaml,
          props.node.key,
          next
        )
      )
    } catch (error) {
      setPatchError(error instanceof Error ? error.message : '无法更新资源选择')
    }
  }

  return (
    <section className={inspectorStyles.section} aria-label="共享资源">
      <div className={inspectorStyles.sectionTitle}>共享资源</div>
      <p className={styles.hint}>
        节点选择资源即可。新任务使用资源库中的当前版本，已有任务不变。
      </p>
      {resourcesQuery.isLoading && <p className={styles.hint}>正在加载资源…</p>}
      {resourcesQuery.isError && (
        <p className={styles.error} role="alert">
          资源列表加载失败，请稍后重试。
        </p>
      )}
      {!resourcesQuery.isLoading &&
        !resourcesQuery.isError &&
        resources.length === 0 && (
          <p className={styles.hint}>资源库暂无可用资源。</p>
        )}
      <div className={styles.list}>
        {resources.map((resource) => {
          const checked = selected.includes(resource.id)
          const stopped = resource.access_status === 'revoked'
          return (
            <label className={styles.option} key={resource.id}>
              <input
                type="checkbox"
                checked={checked}
                disabled={props.readOnly || (stopped && !checked)}
                onChange={(event) => toggle(resource.id, event.target.checked)}
              />
              <span>
                {resource.name}
                {stopped ? '（已停止新任务使用，可从节点移除）' : ''}
              </span>
            </label>
          )
        })}
      </div>
      {patchError && (
        <p className={styles.error} role="alert">
          {patchError}
        </p>
      )}
    </section>
  )
}
