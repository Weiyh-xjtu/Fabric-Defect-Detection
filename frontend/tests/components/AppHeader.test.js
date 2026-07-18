/**
 * AppHeader 组件测试
 *
 * 覆盖：
 *   - 智能对话/检测工作台路由显示当前场景胶囊
 *   - 其他路由不显示
 *   - 未配置全局模型（scene=null）或接口失败时不显示
 */

import { beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, shallowMount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { reactive } from 'vue'

const route = reactive({ path: '/chat' })
const routerPush = vi.fn()

vi.mock('vue-router', () => ({
  useRoute: () => route,
  useRouter: () => ({ push: routerPush }),
}))

const getCurrentScene = vi.fn()

vi.mock('@/api/detection', () => ({
  getCurrentScene: (...args) => getCurrentScene(...args),
}))

import AppHeader from '@/components/layout/AppHeader.vue'

function mountHeader() {
  const pinia = createPinia()
  setActivePinia(pinia)
  return shallowMount(AppHeader, {
    global: {
      plugins: [pinia],
      stubs: {
        ElTooltip: { template: '<span><slot /></span>' },
        ElDropdown: { template: '<div><slot /><slot name="dropdown" /></div>' },
        ElDropdownMenu: { template: '<div><slot /></div>' },
        ElDropdownItem: { template: '<div><slot /></div>' },
        ElAvatar: { template: '<span><slot /></span>' },
        ElIcon: { template: '<span><slot /></span>' },
      },
    },
  })
}

describe('AppHeader 当前场景胶囊', () => {
  beforeEach(() => {
    getCurrentScene.mockReset()
    route.path = '/chat'
  })

  it('智能对话页显示全局模型归属场景', async () => {
    getCurrentScene.mockResolvedValue({
      scene: { id: 1, name: 'fabric', display_name: '织物缺陷检测', category: 'industry' },
      model_version: 'v1.2.0',
      model_name: 'fabric-yolo',
    })
    const wrapper = mountHeader()
    await flushPromises()
    expect(getCurrentScene).toHaveBeenCalledTimes(1)
    expect(wrapper.find('.scene-chip').exists()).toBe(true)
    expect(wrapper.find('.scene-chip-name').text()).toBe('织物缺陷检测')
  })

  it('检测工作台路由同样显示', async () => {
    route.path = '/detection'
    getCurrentScene.mockResolvedValue({
      scene: { id: 1, name: 'fabric', display_name: '织物缺陷检测', category: 'industry' },
    })
    const wrapper = mountHeader()
    await flushPromises()
    expect(wrapper.find('.scene-chip').exists()).toBe(true)
  })

  it('非目标路由不显示也不请求', async () => {
    route.path = '/dashboard'
    const wrapper = mountHeader()
    await flushPromises()
    expect(getCurrentScene).not.toHaveBeenCalled()
    expect(wrapper.find('.scene-chip').exists()).toBe(false)
  })

  it('未配置全局模型时不显示胶囊', async () => {
    getCurrentScene.mockResolvedValue({ scene: null })
    const wrapper = mountHeader()
    await flushPromises()
    expect(wrapper.find('.scene-chip').exists()).toBe(false)
  })

  it('接口失败时静默隐藏', async () => {
    getCurrentScene.mockRejectedValue(new Error('network'))
    const wrapper = mountHeader()
    await flushPromises()
    expect(wrapper.find('.scene-chip').exists()).toBe(false)
  })
})
