import yaml from 'js-yaml'
import { describe, expect, it } from 'vitest'
import {
  patchWorkflowNodeApprovalConfig,
  readApprovalNodeConfig,
} from './workflowStudioYamlDraft.approvalConfig'

const dagYaml = [
  'key: demo',
  'nodes:',
  '  _start:',
  '    type: start',
  '  intake:',
  '    type: code',
  '    capability: intake',
  '    after: [_start]',
  '  gate:',
  '    type: approval',
  '    config: {rework_target: intake, feedback_artifact: review.json}',
  '    after: [intake]',
  '',
].join('\n')

function parseNode(raw: string, key: string): Record<string, unknown> {
  return (
    (yaml.load(raw) as { nodes?: Record<string, Record<string, unknown>> })
      .nodes?.[key] ?? {}
  )
}

describe('readApprovalNodeConfig', () => {
  it('reads the whitelist keys with the backend default for feedback', () => {
    expect(readApprovalNodeConfig(dagYaml, 'gate')).toEqual({
      reworkTarget: 'intake',
      feedbackArtifact: 'review.json',
    })
    // 无 config 的 approval 节点：rework_target 空 + 后端默认文件名。
    const bare = dagYaml.replace(
      '    config: {rework_target: intake, feedback_artifact: review.json}\n',
      ''
    )
    expect(readApprovalNodeConfig(bare, 'gate')).toEqual({
      reworkTarget: '',
      feedbackArtifact: 'review_feedback.json',
    })
  })
})

describe('patchWorkflowNodeApprovalConfig', () => {
  it('overwrites only the given key, keeping the other at its draft value', () => {
    // 只传 reworkTarget：feedback_artifact 保留草稿现值（不物化读侧
    // 默认 review_feedback.json）。
    const out = patchWorkflowNodeApprovalConfig(dagYaml, 'gate', {
      reworkTarget: 'intake',
    })
    expect(parseNode(out, 'gate').config).toEqual({
      rework_target: 'intake',
      feedback_artifact: 'review.json',
    })
    // 只传 feedbackArtifact：rework_target 同理保留。
    const out2 = patchWorkflowNodeApprovalConfig(dagYaml, 'gate', {
      feedbackArtifact: 'notes.md',
    })
    expect(parseNode(out2, 'gate').config).toEqual({
      rework_target: 'intake',
      feedback_artifact: 'notes.md',
    })
  })

  it('seeds the backend default when patching an approval node without config', () => {
    const bare = dagYaml.replace(
      '    config: {rework_target: intake, feedback_artifact: review.json}\n',
      ''
    )
    const out = patchWorkflowNodeApprovalConfig(bare, 'gate', {
      reworkTarget: 'intake',
    })
    expect(parseNode(out, 'gate').config).toEqual({
      rework_target: 'intake',
    })
  })

  it('drops the config block entirely when both keys are explicitly cleared', () => {
    const out = patchWorkflowNodeApprovalConfig(dagYaml, 'gate', {
      reworkTarget: '',
      feedbackArtifact: '',
    })
    expect(parseNode(out, 'gate')).not.toHaveProperty('config')
  })

  it('rejects path separators in feedback_artifact (loader mirror)', () => {
    expect(() =>
      patchWorkflowNodeApprovalConfig(dagYaml, 'gate', {
        reworkTarget: 'intake',
        feedbackArtifact: 'nested/review.json',
      })
    ).toThrow('裸文件名')
  })

  it('refuses to write approval config on non-approval nodes (fail-closed)', () => {
    expect(() =>
      patchWorkflowNodeApprovalConfig(dagYaml, 'intake', {
        reworkTarget: 'intake',
        feedbackArtifact: 'review.json',
      })
    ).toThrow('not an approval node')
  })
})
