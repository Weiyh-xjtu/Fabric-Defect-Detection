import { beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, shallowMount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { nextTick, reactive } from 'vue'
import { ElMessageBox } from 'element-plus'

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
  updateChatSessionTitle: vi.fn((sessionUuid, title) => Promise.resolve({
    session_uuid: sessionUuid,
    title,
  })),
}))

import AppSidebar from '@/components/layout/AppSidebar.vue'
import { useAgentStore } from '@/stores/agent'
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
        ElInput: {
          props: ['modelValue'],
          emits: ['update:modelValue'],
          template: '<input :value="modelValue" @input="$emit(\'update:modelValue\', $event.target.value)" />',
        },
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

  it('可以收起主侧边栏并只保留菜单图标', async () => {
    const wrapper = mountSidebar()

    await wrapper.find('.sidebar-collapse-button').trigger('click')

    expect(wrapper.find('.app-sidebar').classes()).toContain('is-collapsed')
    expect(wrapper.find('.chat-history-section').exists()).toBe(false)
    expect(wrapper.find('.menu-scroll-area').text()).not.toContain('智能对话')
  })

  it('可以单独收起和展开历史对话列表', async () => {
    const wrapper = mountSidebar()

    await wrapper.find('.history-toggle-button').trigger('click')
    expect(wrapper.find('.chat-history-section').classes()).toContain('is-history-collapsed')
    expect(wrapper.find('.session-list').exists()).toBe(false)

    await wrapper.find('.history-toggle-button').trigger('click')
    expect(wrapper.find('.session-list').exists()).toBe(true)
  })

  it('可以按会话标题搜索历史对话', async () => {
    const wrapper = mountSidebar()
    await flushPromises()
    const agentStore = useAgentStore()
    agentStore.sessions = [
      { session_uuid: 'one', title: '布面划痕检测', message_count: 2 },
      { session_uuid: 'two', title: '污渍识别', message_count: 3 },
    ]
    await nextTick()

    await wrapper.find('.history-search-button').trigger('click')
    await wrapper.find('.history-search-input').setValue('划痕')

    const sessionNames = wrapper.findAll('.session-name').map((item) => item.text())
    expect(sessionNames).toEqual(['布面划痕检测'])
  })

  it('可以编辑历史对话标题并立即更新列表', async () => {
    const prompt = vi.spyOn(ElMessageBox, 'prompt').mockResolvedValue({
      value: '更新后的标题',
    })
    const wrapper = mountSidebar()
    await flushPromises()
    const agentStore = useAgentStore()
    agentStore.sessions = [
      { session_uuid: 'rename-me', title: '原标题', message_count: 2 },
    ]
    await nextTick()

    await wrapper.find('.session-edit').trigger('click')
    await flushPromises()

    expect(prompt).toHaveBeenCalled()
    expect(wrapper.find('.session-name').text()).toBe('更新后的标题')
    prompt.mockRestore()
  })
})
