import { useMemo, useEffect, useRef, useState } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import {
  rollbackWorkflowRuntimeConfig,
  saveWorkflowRuntimeConfig,
} from '../../../api'
import { extraQueryKeys } from '../../../lib/queryKeysExtra'
import { useUiStore } from '../../../stores/uiStore'
import { fetchActiveRevisionOrNull } from '../canvas/workflowStudioEmptyState'
import {
  combineYamlStructureRuntime,
  diffRuntimeSliceKeys,
  extractRuntimeSlice,
  hasStructuralYamlChanges,
} from './workflowRuntimeSlice'

export type UseStudioRuntimeChannelResult = {
  /** 草稿相对 active 基线的 runtime 切片差异（点路径，排序）——保存按钮
   * 启停与「已修改」标记的数据源。 */
  runtimeChangedKeys: string[]
  /** 草稿或基线 YAML 语法错误：无法合并保存请求体，保存动作不可用。 */
  runtimeSaveUnavailable: boolean
  runtimeSaving: boolean
  /** 最近一次保存/回滚实际写入的键（后端 changed_keys）——「已生效」标记
   * 与发布复核框信息条的数据源；下一次保存/回滚或卸载前保持。 */
  lastRuntimeSavedKeys: string[]
  /** 运行配置保存：请求体 = active 基线结构 + runtime 来源（默认当前草稿，
   * agent 建议卡传建议 YAML）的 runtime 切片。立即生效，不产生 revision。 */
  saveRuntimeConfig: (runtimeSourceYaml?: string) => Promise<boolean>
  rollbackRuntimeConfig: (version: number) => Promise<boolean>
}

type StudioDataSnapshot = {
  active: unknown
  history: unknown
  workspaces: unknown
}

function errorStatus(error: unknown): number | null {
  return (error as { status?: number } | null)?.status ?? null
}

function errorMessage(error: unknown): string {
  return error instanceof Error && error.message ? error.message : '网络错误'
}

/**
 * 运行通道（M48）：runtime 字段（provider/model/thinking/prompt/prompt_mode/
 * tools/requires_labels + 顶层 execution 默认块）的即改即存通道——绕过草稿
 * 发布链路，PATCH 后 active revision 原地生效并追加配置版本（M47）。
 * 保存成功后：拉取新 active 写入查询缓存（baseline sync 经
 *  markRuntimeChannelSaved 静默推进，不误报外部变更）；草稿无结构编辑时
 * 对齐新 canonical 基线，有结构编辑时保留结构、并入新 runtime 切片
 * （避免之后结构发布把运行配置打回旧值）。
 */
export function useStudioRuntimeChannel(args: {
  workspaceId: string | undefined
  originalYaml: string
  draftYaml: string
  setDraftYaml: (value: string) => void
  markRuntimeChannelSaved: () => void
}): UseStudioRuntimeChannelResult {
  const { workspaceId, originalYaml, draftYaml } = args
  const queryClient = useQueryClient()
  const [runtimeSaving, setRuntimeSaving] = useState(false)
  const [lastRuntimeSavedKeys, setLastRuntimeSavedKeys] = useState<string[]>([])
  // 保存在途期间草稿可能继续被编辑——收尾合并读最新值而不是闭包快照。
  const liveRef = useRef({ originalYaml, draftYaml })
  useEffect(() => {
    liveRef.current = { originalYaml, draftYaml }
  })

  const runtimeChangedKeys = useMemo(() => {
    if (!originalYaml || !draftYaml) return []
    try {
      return diffRuntimeSliceKeys(
        extractRuntimeSlice(originalYaml),
        extractRuntimeSlice(draftYaml)
      )
    } catch {
      return []
    }
  }, [originalYaml, draftYaml])

  const runtimeSaveUnavailable = useMemo(() => {
    if (!originalYaml || !draftYaml) return true
    try {
      extractRuntimeSlice(originalYaml)
      extractRuntimeSlice(draftYaml)
      return false
    } catch {
      return true
    }
  }, [originalYaml, draftYaml])

  async function applyFreshActive(): Promise<string | null> {
    if (!workspaceId) return null
    const fresh = await fetchActiveRevisionOrNull(workspaceId)
    if (!fresh) return null
    const { originalYaml: baseline, draftYaml: currentDraft } = liveRef.current
    args.markRuntimeChannelSaved()
    queryClient.setQueryData<StudioDataSnapshot>(
      extraQueryKeys.workflowStudioData(workspaceId),
      (old) => (old ? { ...old, active: fresh } : old)
    )
    // 无结构编辑：直接对齐新 canonical 基线（基线同步随之 reset，dirty 归零）；
    // 有结构编辑：保留草稿结构、并入刚生效的 runtime 切片——之后的结构发布
    // 才不会把运行配置打回保存前的旧值。
    const nextDraft = hasStructuralYamlChanges(baseline, currentDraft)
      ? combineYamlStructureRuntime(currentDraft, fresh.definition_yaml)
      : fresh.definition_yaml
    if (nextDraft !== currentDraft) args.setDraftYaml(nextDraft)
    return fresh.definition_yaml
  }

  /* r1 P2-1：保存/回滚已生效后的本地刷新（拉新 active + 并草稿）失败不能
   * 报「保存失败」——PATCH 已落库，报失败会误导重试（重试是 no-change
   * 保存，用户以为没生效）。包错分离：刷新失败给「已生效 + 手动刷新」。 */
  async function tryApplyFreshActive(): Promise<boolean> {
    try {
      await applyFreshActive()
      return true
    } catch {
      return false
    }
  }

  async function saveRuntimeConfig(
    runtimeSourceYaml?: string
  ): Promise<boolean> {
    if (!workspaceId || runtimeSaving) return false
    let saveYaml: string
    try {
      saveYaml = combineYamlStructureRuntime(
        originalYaml,
        runtimeSourceYaml ?? draftYaml
      )
    } catch {
      useUiStore
        .getState()
        .showToast('草稿 YAML 有语法错误，修复后才能保存运行配置', 'error')
      return false
    }
    setRuntimeSaving(true)
    try {
      const result = await saveWorkflowRuntimeConfig(workspaceId, saveYaml)
      setLastRuntimeSavedKeys(result.runtime_version?.changed_keys ?? [])
      const refreshed = await tryApplyFreshActive()
      useUiStore
        .getState()
        .showToast(
          refreshed
            ? '运行配置已保存并生效（不产生新版本）'
            : '运行配置已保存并生效；本地刷新失败，请手动刷新页面查看最新配置',
          refreshed ? 'success' : 'error'
        )
      return true
    } catch (error) {
      const message =
        errorStatus(error) === 409
          ? '运行配置保存失败：workflow 刚被其他操作更新，请刷新后重试'
          : `运行配置保存失败：${errorMessage(error)}`
      useUiStore.getState().showToast(message, 'error')
      return false
    } finally {
      setRuntimeSaving(false)
    }
  }

  async function rollbackRuntimeConfig(version: number): Promise<boolean> {
    if (!workspaceId || runtimeSaving) return false
    setRuntimeSaving(true)
    try {
      const result = await rollbackWorkflowRuntimeConfig(workspaceId, version)
      setLastRuntimeSavedKeys(result.runtime_version?.changed_keys ?? [])
      const refreshed = await tryApplyFreshActive()
      useUiStore
        .getState()
        .showToast(
          refreshed
            ? `已回滚到运行配置 v${version}（作为新版本追加）`
            : `已回滚到运行配置 v${version} 并生效；本地刷新失败，请手动刷新页面`,
          refreshed ? 'success' : 'error'
        )
      return true
    } catch (error) {
      useUiStore
        .getState()
        .showToast(`运行配置回滚失败：${errorMessage(error)}`, 'error')
      return false
    } finally {
      setRuntimeSaving(false)
    }
  }

  return {
    runtimeChangedKeys,
    runtimeSaveUnavailable,
    runtimeSaving,
    lastRuntimeSavedKeys,
    saveRuntimeConfig,
    rollbackRuntimeConfig,
  }
}
