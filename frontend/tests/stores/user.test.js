import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'

const mocks = vi.hoisted(() => ({
  logoutApi: vi.fn(() => Promise.resolve()),
}))

vi.mock('@/api/auth', () => ({
  getUserInfoApi: vi.fn(),
  loginApi: vi.fn(),
  logoutApi: (...args) => mocks.logoutApi(...args),
  refreshSessionApi: vi.fn(),
}))

import { useUserStore } from '@/stores/user'


describe('user permission getters', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    localStorage.clear()
    setActivePinia(createPinia())
  })

  it('uses the permission union returned by the backend', () => {
    const store = useUserStore()
    store.user = {
      permissions: ['chat:use', 'history:read:any'],
      is_superuser: false,
    }

    expect(store.hasPermission('chat:use')).toBe(true)
    expect(store.hasPermission('detection:execute')).toBe(false)
    expect(store.hasAnyPermission(['history:read:own', 'history:read:any'])).toBe(true)
  })

  it('keeps the superuser bypass', () => {
    const store = useUserStore()
    store.user = { permissions: [], is_superuser: true }

    expect(store.hasPermission('model:manage')).toBe(true)
    expect(store.hasAnyPermission(['user:manage'])).toBe(true)
  })

  it('退出时先将当前 Access Token 交给服务端再清理本地会话', () => {
    const store = useUserStore()
    store.token = 'current-access-token'
    store.user = { id: 1, username: 'logout-user' }

    store.logout()

    expect(mocks.logoutApi).toHaveBeenCalledWith('current-access-token')
    expect(store.token).toBe('')
    expect(store.user).toBeNull()
  })
})
