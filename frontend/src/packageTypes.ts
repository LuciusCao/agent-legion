import type { components } from './generated/api'

type ApiSchemas = components['schemas']

export type PackageItem = ApiSchemas['PackageItemResponse']
export type PackagesResponse = ApiSchemas['PackagesResponse']
export type PackageUpdate = ApiSchemas['PackageUpdate']
export type PackageUpdateResponse = ApiSchemas['PackageUpdateResponse']
export type PackageDeleteResponse = ApiSchemas['PackageDeleteResponse']
export type WorkspacePackageUpdate = ApiSchemas['WorkspacePackageUpdate']
export type WorkspacePackageUpdateResponse =
  ApiSchemas['WorkspacePackageUpdateResponse']
export type WorkspacePackageDeleteResponse =
  ApiSchemas['WorkspacePackageDeleteResponse']
export type WorkspacePackagesResponse = ApiSchemas['WorkspacePackagesResponse']
