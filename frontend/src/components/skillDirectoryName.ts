/** key → 目录输入回显值（codex 三轮 P2 on #427）：目录输入恒以
 * 「<skills_root>/<workspaceId>/」只读前缀展示，常规 workspace 绑定
 * （如 ws-1/write-script）首段与前缀重复，只回显余段——回显全段会让
 * 用户点「校验」拼出 ws-1/ws-1/write-script，把有效绑定报成目录不
 * 存在；group 形态（首段是技能根下的 group 名，如 demo 的
 * education-video-problems-generation/write-script）回显全段——剥掉首段
 * 就不再是技能根下的原相对路径。key 为空（未绑定）回空串。
 * （自 SkillSelector.tsx 拆出，文件预算，codex 四轮 P1 on #427。） */
export function directoryNameFromKey(key: string, workspaceId: string): string {
  const relative = key.trim().replace(/^\/+/, '')
  const ws = `${workspaceId}/`
  return relative.startsWith(ws) ? relative.slice(ws.length) : relative
}
