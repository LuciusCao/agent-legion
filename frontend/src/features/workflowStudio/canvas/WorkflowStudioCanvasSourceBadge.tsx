import { useMemo } from 'react'
import { Chip } from '@mui/material'
import { useStudioState } from '../shared/studioStateContext'
import { workflowYamlToDefinitionRecord } from './workflowYamlDraftRecord'

/** 画布数据源标识：草稿 YAML 编辑中途非法时画布回退已发布版本，换警示
 * 色说明（不报错、不清空画布）。「草稿（未发布变更 N）」chip 随 #804
 * 定案退役——未发布变更/校验状态由左岛状态 chip 唯一承接；revision 模式
 * 的「只读 vN」标识亦在左岛 chip。顶层 execution 缺失的画布级提示随
 * #804 抽屉化退役（#333）：缺口由节点徽标（DagNodeHeader 的 execution
 * 缺失标记）承载。
 * 轮 6 H4：compare 传输失败（compareState='error'）必须有可见态 + 重试
 * 出口——否则 hasCompareChanges=false 静默禁发布，界面零提示（隐形
 * 死锁），恢复只能靠再编辑。 */
export function WorkflowStudioCanvasSourceBadge() {
  const studio = useStudioState()
  const parseFailed = useMemo(
    () =>
      studio.viewMode === 'draft' &&
      studio.definitionYaml.trim() !== '' &&
      workflowYamlToDefinitionRecord(studio.definitionYaml) === null,
    [studio.viewMode, studio.definitionYaml]
  )
  return (
    <>
      {studio.viewMode === 'draft' && parseFailed ? (
        <Chip
          size="small"
          color="warning"
          label="草稿 YAML 未完成解析，画布暂显示已发布版本"
        />
      ) : null}
      {studio.compareState === 'error' ? (
        <Chip
          size="small"
          color="error"
          label="草稿对比失败"
          title="与已发布版本的对比请求失败——点击重试"
          onClick={() => studio.retryCompare()}
        />
      ) : null}
    </>
  )
}
