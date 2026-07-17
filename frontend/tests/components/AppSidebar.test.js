import { beforeEach, describe, expect, it, vi } from 'vitest'
import { shallowMount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { nextTick, reactive } from 'vue'

const route = reactive({ path: '/chat' })
const routerPush = vi.fn()

vi.mock('vue-router', () => ({
  useRoute: () => route,
  useRouter: () => ({ push: routerPush }),
}))

vi.mock('@/api/chat', () => ({
  getChatSessions: vi.fn(() => Promise.resolve([])),
  getChatSessionHistory: vi.fn(),
  deleteChatSession: vi.fn(),
}))

import AppSidebar from '@/components/layout/AppSidebar.vue'
import { useUserStore } from '@/stores/user'

function mountSidebar() {
  const pinia = createPinia()
  setActivePinia(pinia)
  const userStore = useUserStore()
  userStore.user = {
    permissions: [
      'chat:use',
      'detection:execute',
      'model:manage',
      'history:read:any',
      'knowledge:manage',
      'dashboard:read:any',
      'user:manage',
    ],
    is_superuser: false,
  }

  return shallowMount(AppSidebar, {
    global: {
      plugins: [pinia],
      directives: { loading: () => {} },
      stubs: {
        ElMenu: { template: '<nav><slot /></nav>' },
        ElMenuItem: { template: '<div><slot /></div>' },
        ElIcon: { template: '<span><slot /></span>' },
        ElButton: { template: '<button><slot /></button>' },
      },
    },
  })
}

describe('AppSidebar 独立滚动布局', () => {
  beforeEach(() => {
    route.path = '/chat'
    routerPush.mockReset()
  })

  it('聊天页面同时渲染菜单滚动区和历史滚动区', () => {
    const wrapper = mountSidebar()

    expect(wrapper.find('.app-sidebar').classes()).toContain('has-chat-history')
    expect(wrapper.find('.menu-scroll-area').exists()).toBe(true)
    expect(wrapper.find('.chat-history-section').exists()).toBe(true)
    expect(wrapper.find('.session-list').exists()).toBe(true)
  })

  it('非聊天页面仅保留全高菜单滚动区', async () => {
    const wrapper = mountSidebar()
    route.path = '/dashboard'
    await nextTick()

    expect(wrapper.find('.app-sidebar').classes()).not.toContain('has-chat-history')
    expect(wrapper.find('.menu-scroll-area').exists()).toBe(true)
    expect(wrapper.find('.chat-history-section').exists()).toBe(false)
  })
})
