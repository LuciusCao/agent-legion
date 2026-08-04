import { create } from 'zustand'
import { setUnauthorizedHandler } from '../api/requestAuth'
import {
  bootstrap as apiBootstrap,
  fetchBootstrapStatus,
  fetchMe,
  login as apiLogin,
  logout as apiLogout,
} from '../api/authApi'
import type { UserResponse } from '../api/authApi'

export type AuthStatus = 'unknown' | 'authenticated' | 'anonymous'

type AuthState = {
  user: UserResponse | null
  status: AuthStatus
  bootstrapAvailable: boolean | null

  initialize: () => Promise<void>
  login: (username: string, password: string) => Promise<void>
  logout: () => Promise<void>
  bootstrap: (
    username: string,
    password: string,
    displayName?: string
  ) => Promise<void>
}

export const useAuthStore = create<AuthState>((set) => ({
  user: null,
  status: 'unknown',
  bootstrapAvailable: null,

  async initialize() {
    const [bootstrapStatus, me] = await Promise.all([
      fetchBootstrapStatus().catch(() => null),
      fetchMe().catch(() => null),
    ])
    set({
      bootstrapAvailable: bootstrapStatus ? bootstrapStatus.available : null,
      user: me,
      status: me ? 'authenticated' : 'anonymous',
    })
  },

  async login(username: string, password: string) {
    const user = await apiLogin({ username, password })
    set({ user, status: 'authenticated' })
  },

  async logout() {
    try {
      await apiLogout()
    } finally {
      set({ user: null, status: 'anonymous' })
    }
  },

  async bootstrap(username: string, password: string, displayName?: string) {
    const user = await apiBootstrap({
      username,
      password,
      display_name: displayName ?? '',
    })
    set({ user, status: 'authenticated', bootstrapAvailable: false })
  },
}))

// Registered on module load: any API 401 outside the auth endpoints marks the
// session as expired and sends the user back to /login.
setUnauthorizedHandler(() => {
  useAuthStore.setState({ user: null, status: 'anonymous' })
  try {
    window.location.assign('/login')
  } catch {
    // ignore — environments without navigation support (e.g. jsdom)
  }
})
