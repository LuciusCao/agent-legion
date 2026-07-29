import type { WorkflowNodeRecord } from '../../types'

export function buildWorkflowNodePromptPreview(
  node: WorkflowNodeRecord,
  skillKey: string,
  additionalPrompt: string
) {
  const lines = [
    'Execute the loaded node skill for this Agent Legion workflow job.',
    '',
    'Job ID: <job_id>',
    `Node: ${node.key}`,
    'Working directory: <job_working_directory>',
    `Skill directory: <skill_root>/${skillKey}`,
    `Validator script: <skill_root>/${skillKey}/scripts/validate_output.py`,
    '',
    'Declared inputs:',
    ...node.inputs.map((item) => `- ${item}`),
    '',
    'Required outputs:',
    ...node.outputs.map((item) => `- ${item}`),
    '',
    'Write required outputs directly into the working directory. Do not modify inputs or create undeclared root-level artifacts. Finish after all required outputs are written and correct.',
  ]
  if (additionalPrompt.trim()) {
    lines.push('', 'Additional node instructions:', additionalPrompt.trim())
  }
  return `${lines.join('\n')}\n`
}
