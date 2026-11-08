import { describe, expect, it } from 'vitest'
import {
  categoryLabelForError,
  formatEdgeChange,
  formatIntakeChange,
  formatMetadataChange,
  formatNodeChange,
} from './workflowStudioChangeFormatters'
import type {
  EdgeChangeGroup,
  IntakeChangeGroup,
  MetadataChangeGroup,
  NodeChangeGroup,
} from './workflowStudioChanges'

const nodeChange: NodeChangeGroup = {
  type: 'modified',
  nodeKey: 'fan_out',
  label: '分片节点',
  nodeType: 'agent',
  fields: ['node_type', 'after', 'shard', 'reduce', 'tools', 'config'],
  severity: 'warning',
}

const edgeChange: EdgeChangeGroup = {
  type: 'condition_changed',
  source: 'fan_out',
  target: 'collect',
  beforeCondition: '$.ok == true',
  afterCondition: '$.ok == false',
  severity: 'breaking',
}

describe('workflow change formatter equivalence', () => {
  it('keeps every compare field label and unknown-field fallback exact', () => {
    expect(formatNodeChange(nodeChange)).toBe(
      '分片节点: 节点类型、依赖顺序、分片、聚合、工具白名单、节点配置值'
    )
    expect(
      formatNodeChange({ ...nodeChange, fields: ['config_schema', 'future'] })
    ).toBe('分片节点: 配置 Schema、future')
    expect(formatNodeChange({ ...nodeChange, type: 'added', fields: [] })).toBe(
      '新增节点 分片节点'
    )
  })

  it('keeps edge, intake, metadata and error category text exact', () => {
    expect(formatEdgeChange(edgeChange)).toBe(
      'fan_out → collect: $.ok == true → $.ok == false'
    )
    expect(formatEdgeChange({ ...edgeChange, type: 'reordered' })).toBe(
      '边顺序调整（边集合不变）'
    )
    expect(
      formatEdgeChange({
        ...edgeChange,
        type: 'added',
        beforeCondition: null,
        afterCondition: null,
      })
    ).toBe('新增边 fan_out → collect')

    const intake: IntakeChangeGroup = {
      type: 'field_added',
      modeKey: 'default',
      fieldKey: 'question',
      severity: 'info',
    }
    expect(formatIntakeChange(intake)).toBe('default / question: 新增字段')
    expect(formatIntakeChange({ ...intake, fieldKey: null })).toBe(
      'default: 新增字段'
    )

    const metadata: MetadataChangeGroup = {
      type: 'modified',
      field: 'label',
      beforeValue: 'Old',
      afterValue: 'New',
      severity: 'info',
    }
    expect(formatMetadataChange(metadata)).toBe('Workflow 名称: Old → New')
    expect(formatMetadataChange({ ...metadata, field: 'future' })).toBe(
      'future: Old → New'
    )
    expect(categoryLabelForError('yaml')).toBe('YAML解析')
    expect(categoryLabelForError('future')).toBe('future')
  })
})
