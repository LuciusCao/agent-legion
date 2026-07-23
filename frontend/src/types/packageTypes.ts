import type { components } from '../generated/api'

type ApiSchemas = components['schemas']

export type WorkspacePackageItem = ApiSchemas['WorkspacePackageItemResponse']
export type WorkspacePackageUpdate = ApiSchemas['WorkspacePackageUpdate']
export type WorkspacePackageUpdateResponse =
  ApiSchemas['WorkspacePackageUpdateResponse']
export type WorkspacePackageDeleteResponse =
  ApiSchemas['WorkspacePackageDeleteResponse']
export type WorkspacePackagesResponse = ApiSchemas['WorkspacePackagesResponse']
