export const TYPE_LABELS: Record<string, string> = {
  added: '新增',
  removed: '删除',
  modified: '修改',
  condition_changed: '条件变更',
  mode_changed: '模式变更',
  field_added: '新增字段',
  field_removed: '删除字段',
  reordered: '顺序调整',
}

export const FIELD_LABELS: Record<string, string> = {
  label: '标签',
  capability: '能力',
  inputs: '输入',
  outputs: '输出',
  terminal: '终点',
  // #418：compare 已补 config/config_schema 比对（风险档 warning），
  // 展示层同步给中文标签，避免变更列表里裸字段名。
  config: '节点配置值',
  config_schema: '配置 Schema',
  execution: '执行配置',
  skill: 'Skill 绑定',
  // #1114 codex P2：谓词覆盖的字段全量给中文标签（runtime 是 #1114 引入的
  // 摘要字段，tools/requires_labels 是本次补报的档案结构字段）。
  runtime: '执行 Runtime',
  tools: '工具白名单',
  requires_labels: 'Worker 标签要求',
  accepted_item_types: '入口条目类型',
  text_input: '直接输入需求的呈现配置',
  node_type: '节点类型',
  after: '依赖顺序',
  shard: '分片',
  reduce: '聚合',
}

export const CATEGORY_LABELS: Record<string, string> = {
  yaml: 'YAML解析',
  schema: '结构校验',
  structure: '结构',
  executor: '执行器',
  revision: '版本',
}

export function categoryLabelForError(category: string): string {
  return CATEGORY_LABELS[category] ?? category
}
