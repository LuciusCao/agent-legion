/** SkillSelector 的目录候选 datalist（#327）：列出 <skills_root>/<workspace>/
 * 下已有的 skill 目录，自由输入仍然可用。自 SkillSelector 拆出（文件预算），
 * 模式对齐 WorkflowRuntimeOptionsDatalist。 */
export function SkillDirectoryDatalist(props: {
  id: string
  options: string[]
}) {
  return (
    <datalist id={props.id}>
      {props.options.map((option) => (
        <option key={option} value={option} />
      ))}
    </datalist>
  )
}
