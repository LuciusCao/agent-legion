import type { components } from '../generated/api'

type ApiSchemas = components['schemas']

export type AgentDefinition = ApiSchemas['AgentDefinitionResponse']
export type AgentCatalogResponse = ApiSchemas['AgentCatalogResponse']
export type SkillDetail = ApiSchemas['SkillDetailResponse']
export type SkillFile = ApiSchemas['SkillFileResponse']
export type WorkspaceExecutionConfiguration =
  ApiSchemas['WorkspaceExecutionConfigurationResponse']
