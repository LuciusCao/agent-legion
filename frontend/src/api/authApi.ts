import { api } from './core'
import type { components } from '../generated/api'

export type UserResponse = components['schemas']['UserResponse']
export type MemberResponse = components['schemas']['MemberResponse']
export type UserPatchRequest = components['schemas']['UserPatchRequest']

type LoginRequest = components['schemas']['LoginRequest']
type LoginResponse = components['schemas']['LoginResponse']
type MeResponse = components['schemas']['MeResponse']
type BootstrapRequest = components['schemas']['BootstrapRequest']
type BootstrapStatusResponse = components['schemas']['BootstrapStatusResponse']
type UsersResponse = components['schemas']['UsersResponse']
type UserCreateRequest = components['schemas']['UserCreateRequest']
type MembersResponse = components['schemas']['MembersResponse']
type MemberPutRequest = components['schemas']['MemberPutRequest']

export async function login(input: LoginRequest): Promise<UserResponse> {
  const data = await api<LoginResponse>('/api/auth/login', {
    method: 'POST',
    body: JSON.stringify(input),
  })
  return data.user
}

export async function logout(): Promise<void> {
  await api<MeResponse>('/api/auth/logout', { method: 'POST' })
}

export async function fetchMe(): Promise<UserResponse> {
  const data = await api<MeResponse>('/api/auth/me')
  return data.user
}

export async function fetchBootstrapStatus(): Promise<BootstrapStatusResponse> {
  return api<BootstrapStatusResponse>('/api/auth/bootstrap')
}

export async function bootstrap(
  input: BootstrapRequest
): Promise<UserResponse> {
  const data = await api<LoginResponse>('/api/auth/bootstrap', {
    method: 'POST',
    body: JSON.stringify(input),
  })
  return data.user
}

export async function listUsers(): Promise<UserResponse[]> {
  const data = await api<UsersResponse>('/api/users')
  return data.users ?? []
}

export async function createUser(
  input: UserCreateRequest
): Promise<UserResponse> {
  return api<UserResponse>('/api/users', {
    method: 'POST',
    body: JSON.stringify(input),
  })
}

export async function updateUser(
  userId: string,
  patch: UserPatchRequest
): Promise<UserResponse> {
  return api<UserResponse>(`/api/users/${encodeURIComponent(userId)}`, {
    method: 'PATCH',
    body: JSON.stringify(patch),
  })
}

export async function listMembers(
  workspaceId: string
): Promise<MemberResponse[]> {
  const data = await api<MembersResponse>(
    `/api/workspaces/${encodeURIComponent(workspaceId)}/members`
  )
  return data.members ?? []
}

export async function putMember(
  workspaceId: string,
  input: MemberPutRequest
): Promise<MemberResponse[]> {
  const data = await api<MembersResponse>(
    `/api/workspaces/${encodeURIComponent(workspaceId)}/members`,
    { method: 'PUT', body: JSON.stringify(input) }
  )
  return data.members ?? []
}

export async function removeMember(
  workspaceId: string,
  userId: string
): Promise<MemberResponse[]> {
  const data = await api<MembersResponse>(
    `/api/workspaces/${encodeURIComponent(workspaceId)}/members/${encodeURIComponent(userId)}`,
    { method: 'DELETE' }
  )
  return data.members ?? []
}
