import type { components } from '../generated/api'

type ApiSchemas = components['schemas']

export type AgentDefinition = ApiSchemas['AgentDefinitionResponse']
export type ExecutorCatalogResponse = ApiSchemas['ExecutorCatalogResponse']
export type SkillDetail = ApiSchemas['SkillDetailResponse']
export type SkillFile = ApiSchemas['SkillFileResponse']
export type WorkspaceExecutorConfiguration =
  ApiSchemas['WorkspaceExecutorConfigurationResponse']
