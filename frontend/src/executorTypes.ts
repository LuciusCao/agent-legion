import type { components } from './generated/api'

type ApiSchemas = components['schemas']

export type ExecutorDefinition = ApiSchemas['ExecutorDefinitionResponse']
export type ExecutorAllocation = ApiSchemas['ExecutorAllocationResponse']
export type ExecutorCatalogResponse = ApiSchemas['ExecutorCatalogResponse']
export type WorkspaceExecutorConfiguration =
  ApiSchemas['WorkspaceExecutorConfigurationResponse']
