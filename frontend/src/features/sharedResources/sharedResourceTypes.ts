import type { components } from '../../generated/api'

type ApiSchemas = components['schemas']

export type SharedResource = ApiSchemas['SharedResourceRecord']
export type SharedResourceListResponse =
  ApiSchemas['SharedResourceListResponse']
export type SharedResourceResponse = ApiSchemas['SharedResourceResponse']
export type SharedResourceSourceInput = ApiSchemas['SharedResourceSourceInput']
export type SharedResourceVersion = ApiSchemas['SharedResourceVersionRecord']
export type SharedResourceVersionListResponse =
  ApiSchemas['SharedResourceVersionListResponse']
export type SharedResourceVersionResponse =
  ApiSchemas['SharedResourceVersionResponse']
export type SharedResourceSelectionResponse =
  ApiSchemas['SharedResourceSelectionResponse']
export type SharedResourceShareResponse =
  ApiSchemas['SharedResourceShareResponse']
export type SharedResourceGrantListResponse =
  ApiSchemas['SharedResourceGrantListResponse']
export type SharedResourceGrantResponse =
  ApiSchemas['SharedResourceGrantResponse']
export type SharedResourceDeleteVersionResponse =
  ApiSchemas['SharedResourceDeleteVersionResponse']
export type RunSharedResource = ApiSchemas['RunSharedResourceRecord']
export type RunDetailResponse = ApiSchemas['RunDetailResponse']
export type MaterialBundleListResponse =
  ApiSchemas['MaterialBundleListResponse']
