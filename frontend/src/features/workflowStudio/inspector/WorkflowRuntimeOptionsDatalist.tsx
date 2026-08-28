/** provider/model 候选 datalist：在线 Worker 实际声明的取值，自由输入
 * 仍然可用（自 WorkflowRuntimeInheritedField 拆出，文件预算）。 */
export function WorkflowRuntimeOptionsDatalist(props: {
  id?: string
  options?: string[]
}) {
  if (!props.id) return null
  return (
    <datalist id={props.id}>
      {(props.options ?? []).map((option) => (
        <option key={option} value={option} />
      ))}
    </datalist>
  )
}
