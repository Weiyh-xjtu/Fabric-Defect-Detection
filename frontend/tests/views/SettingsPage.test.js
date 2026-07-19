import { beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, shallowMount } from '@vue/test-utils'
import ElementPlus, { ElMessage } from 'element-plus'

const mocks = vi.hoisted(() => ({
  requestGet: vi.fn(),
  requestPut: vi.fn(),
  uploadUserAvatar: vi.fn(),
  removeUserAvatar: vi.fn(),
  fetchUserInfo: vi.fn(),
  logout: vi.fn(),
  routerReplace: vi.fn(),
  store: {
    avatar: 'https://minio.test/new-avatar.jpg',
    fetchUserInfo: null,
    logout: null,
  },
}))

mocks.store.fetchUserInfo = mocks.fetchUserInfo
mocks.store.logout = mocks.logout

vi.mock('@/utils/request', () => ({
  default: {
    get: (...args) => mocks.requestGet(...args),
    put: (...args) => mocks.requestPut(...args),
  },
}))

vi.mock('@/api/user', () => ({
  uploadUserAvatar: (...args) => mocks.uploadUserAvatar(...args),
  removeUserAvatar: (...args) => mocks.removeUserAvatar(...args),
}))

vi.mock('@/stores/user', () => ({
  useUserStore: () => mocks.store,
}))

vi.mock('vue-router', () => ({
  useRouter: () => ({ replace: mocks.routerReplace }),
}))

import SettingsPage from '@/views/SettingsPage.vue'

function mountPage() {
  return shallowMount(SettingsPage, {
    global: { plugins: [ElementPlus] },
  })
}

describe('SettingsPage avatar editor', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mocks.requestGet.mockResolvedValue({
      username: 'fabric_user',
      email: 'fabric@example.com',
      phone: '',
      avatar: '',
      created_at: '2026-07-19T12:00:00',
    })
    mocks.uploadUserAvatar.mockResolvedValue({
      user: { avatar: 'https://minio.test/new-avatar.jpg' },
    })
    mocks.fetchUserInfo.mockResolvedValue()
    mocks.requestPut.mockResolvedValue({
      user: {
        username: 'fabric_renamed',
        email: 'fabric@example.com',
        phone: '',
      },
    })
  })

  it('选择合法图片后上传并刷新全局用户信息', async () => {
    const wrapper = mountPage()
    await flushPromises()
    const file = new File(['avatar'], 'avatar.png', { type: 'image/png' })
    const input = { files: [file], value: 'avatar.png' }

    await wrapper.vm.handleAvatarSelected({ target: input })

    expect(mocks.uploadUserAvatar).toHaveBeenCalledWith(file)
    expect(mocks.fetchUserInfo).toHaveBeenCalledOnce()
    expect(wrapper.vm.profileForm.avatar).toBe('https://minio.test/new-avatar.jpg')
    expect(input.value).toBe('')
    expect(ElMessage.success).toHaveBeenCalledWith('头像已更新')
  })

  it('在前端拦截不支持的头像格式', async () => {
    const wrapper = mountPage()
    await flushPromises()
    const file = new File(['avatar'], 'avatar.gif', { type: 'image/gif' })
    const input = { files: [file], value: 'avatar.gif' }

    await wrapper.vm.handleAvatarSelected({ target: input })

    expect(mocks.uploadUserAvatar).not.toHaveBeenCalled()
    expect(input.value).toBe('')
    expect(ElMessage.warning).toHaveBeenCalledWith(
      '头像仅支持 JPG、PNG 或 WebP 格式',
    )
  })

  it('提交个人资料时包含可编辑用户名并刷新用户状态', async () => {
    const wrapper = mountPage()
    await flushPromises()
    wrapper.vm.profileFormRef = {
      validate: vi.fn().mockResolvedValue(true),
    }
    wrapper.vm.profileForm.username = 'fabric_renamed'

    await wrapper.vm.updateProfile()

    expect(mocks.requestPut).toHaveBeenCalledWith('/user/profile', null, {
      params: {
        username: 'fabric_renamed',
        email: 'fabric@example.com',
        phone: '',
      },
    })
    expect(mocks.fetchUserInfo).toHaveBeenCalledOnce()
    expect(wrapper.vm.profileForm.username).toBe('fabric_renamed')
    expect(ElMessage.success).toHaveBeenCalledWith('个人信息已更新')
  })
})
