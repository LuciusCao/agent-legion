const k = (name: string, id: string) => [name, id] as const

/** Studio 与其实体相关的 query key；由 queryKeysExtra 聚合为统一出口。 */
export const studioQueryKeys = {
  // Studio DAG/Inspector 共享的 Agent 目录（P-0.5：executors 半区已退役）；
  // 面板发布/归档后失效重取。
  studioAgentCatalog: (workspaceId: string) =>
    ['studioAgentCatalog', workspaceId] as const,
  // Studio 节点详情的技能文件预览；ref 进 key（版本切换重取），Studio 对话
  // turn_end 按首段 'studioSkillDetail' 前缀整体失效（useStudioChat）。
  // workspaceId 进 key（#745）：skill 读取按 workspace 鉴权，切 workspace
  // 后同 key 不同 scope 的缓存不得复用。
  studioSkillDetail: (
    skillKey: string,
    workspaceId: string,
    ref: string | null
  ) => ['studioSkillDetail', skillKey, workspaceId, ref] as const,
  // #410：节点检查器 Skill 区块 latest 绑定的实际执行版本回显（最近 run
  // 的 skill_version）。skill 进 key（codex 四轮 P1 on #427）：换绑后绑定
  // key 变化即重新查询，不复用旧 key 的缓存；查询本身也按该列过滤
  // （schema v75 的 node_runs.skill）。运行完成路径按 'nodeRuns' 前缀整体
  // 失效（useWorkspaceEvents 的 job 更新事件，codex 四轮 P2 on #427）。
  nodeRuns: (ws: string, nodeKey: string, skill: string) =>
    ['nodeRuns', ws, nodeKey, skill] as const,
  // Studio 节点运行 Prompt 预览；草稿 YAML 进 key（编辑 debounce 后重取）。
  studioNodePromptPreview: (ws: string, nodeKey: string, yaml: string) =>
    ['studioNodePromptPreview', ws, nodeKey, yaml] as const,
  workflowStudioData: (workspaceId: string) =>
    ['workflowStudioData', workspaceId] as const,
  // Studio 编辑器的服务端持久化草稿（GET/PUT workflow-draft）。
  workflowStudioDraft: (workspaceId: string) =>
    ['workflowStudioDraft', workspaceId] as const,
  agentDefinitions: (workspaceId: string) => k('agentDefinitions', workspaceId),
  agentProvenance: (workspaceId: string) => k('agentProvenance', workspaceId),
  // Agent 发起的 workflow 发布请求（#416）：轮询 pending 弹确认对话框。
  studioPublishRequest: (ws: string) => k('studioPublishRequest', ws),
  // workspace 设置页「Skill 共享材料」只读视图（#643）。
  workspaceSharedMaterials: (workspaceId: string) =>
    k('workspaceSharedMaterials', workspaceId),
}
