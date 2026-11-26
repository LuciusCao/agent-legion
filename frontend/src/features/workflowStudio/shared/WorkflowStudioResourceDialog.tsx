import { useState } from 'react'
import {
  Button,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
} from '@mui/material'
import { useQuery } from '@tanstack/react-query'
import { useParams, useSearchParams } from 'react-router-dom'
import { listSharedResources } from '../../../api/sharedResources'
import { sharedResourceKeys } from '../../sharedResources/sharedResourceQueryKeys'
import { useUiStore } from '../../../stores/uiStore'
import {
  parseWorkflowNode,
  patchWorkflowNodeSharedResources,
} from './workflowStudioYamlDraft'
import { normalizeStringList } from './workflowStudioYamlDraft.parse'
import { useStudioState } from './studioStateContext'
import styles from '../../sharedResources/SharedResources.module.css'

export function WorkflowStudioResourceDialog() {
  const { workspaceId = '' } = useParams<{ workspaceId: string }>()
  const [searchParams, setSearchParams] = useSearchParams()
  const resourceId = searchParams.get('resource') ?? ''
  const studio = useStudioState()
  const showToast = useUiStore((state) => state.showToast)
  const [selectedNode, setSelectedNode] = useState('')
  const [error, setError] = useState('')
  const resourcesQuery = useQuery({
    queryKey: sharedResourceKeys.list(workspaceId),
    queryFn: ({ signal }) => listSharedResources(workspaceId, signal),
    enabled: Boolean(workspaceId && resourceId),
  })
  const resource = resourcesQuery.data?.resources.find(
    (item) => item.id === resourceId
  )
  const nodes = (studio.workflow?.nodes ?? []).filter(
    (node) => (node.node_type ?? 'code') === 'code'
  )
  const nodeKey = nodes.some((node) => node.key === selectedNode)
    ? selectedNode
    : (nodes[0]?.key ?? '')

  const close = () => {
    const next = new URLSearchParams(searchParams)
    next.delete('resource')
    setSearchParams(next, { replace: true })
    setError('')
  }
  const add = () => {
    if (!resource || !nodeKey || studio.readOnly) return
    try {
      const node = parseWorkflowNode(studio.definitionYaml, nodeKey)
      const selected = normalizeStringList(node?.shared_resources)
      if (!selected.includes(resource.id)) {
        studio.setDefinitionYaml(
          patchWorkflowNodeSharedResources(studio.definitionYaml, nodeKey, [
            ...selected,
            resource.id,
          ])
        )
      }
      studio.setSelectedNodeKey(nodeKey)
      showToast('已添加到工作流草稿，保存发布后才会用于新任务。', 'success')
      close()
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : '无法添加到工作流')
    }
  }

  return (
    <Dialog open={Boolean(resourceId)} onClose={close} fullWidth maxWidth="sm">
      <DialogTitle>添加到工作流</DialogTitle>
      <DialogContent>
        <div className={styles.form}>
          {resourcesQuery.isLoading && (
            <p className={styles.help}>正在加载资源…</p>
          )}
          {resourcesQuery.isError && (
            <p className={styles.error} role="alert">
              资源加载失败，请关闭后重试。
            </p>
          )}
          {!resourcesQuery.isLoading &&
            !resourcesQuery.isError &&
            !resource && (
              <p className={styles.error} role="alert">
                资源不存在，或你没有查看权限。
              </p>
            )}
          {resource?.access_status === 'revoked' && (
            <p className={`${styles.notice} ${styles.warningNotice}`}>
              这个资源已停止用于新任务，不能添加到工作流。
            </p>
          )}
          {studio.readOnly && (
            <p className={`${styles.notice} ${styles.warningNotice}`}>
              历史版本只能查看，请返回工作流草稿后添加。
            </p>
          )}
          {resource && nodes.length === 0 && (
            <p className={styles.help}>当前工作流没有可添加资源的代码节点。</p>
          )}
          {resource && nodes.length > 0 && (
            <label className={styles.field}>
              使用资源的节点
              <select
                className={styles.select}
                value={nodeKey}
                onChange={(event) => setSelectedNode(event.target.value)}
              >
                {nodes.map((node) => (
                  <option key={node.key} value={node.key}>
                    {node.label || node.key}
                  </option>
                ))}
              </select>
            </label>
          )}
          <p className={styles.help}>
            添加后仍需在 Workflow Studio
            保存并发布。创建任务时会使用资源库的当前版本。
          </p>
          {error && (
            <p className={styles.error} role="alert">
              {error}
            </p>
          )}
        </div>
      </DialogContent>
      <DialogActions>
        <Button onClick={close}>取消</Button>
        <Button
          variant="contained"
          disabled={
            !resource ||
            resource.access_status === 'revoked' ||
            !nodeKey ||
            studio.readOnly
          }
          onClick={add}
        >
          添加到工作流
        </Button>
      </DialogActions>
    </Dialog>
  )
}
