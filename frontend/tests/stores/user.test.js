import { beforeEach, describe, expect, it } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'

import { useUserStore } from '@/stores/user'


describe('user permission getters', () => {
  beforeEach(() => {
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
})
