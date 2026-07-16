import { beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, shallowMount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import ElementPlus from 'element-plus'

const getUserList = vi.fn()
const getRoleList = vi.fn()
const updateUserRoles = vi.fn()
const updateUserStatus = vi.fn()
const routerReplace = vi.fn()

vi.mock('@/api/user', () => ({
  getUserList: (...args) => getUserList(...args),
  getRoleList: (...args) => getRoleList(...args),
  updateUserRoles: (...args) => updateUserRoles(...args),
  updateUserStatus: (...args) => updateUserStatus(...args),
}))

vi.mock('vue-router', async () => {
  const actual = await vi.importActual('vue-router')
  return {
    ...actual,
    useRouter: () => ({ replace: routerReplace }),
  }
})

vi.mock('element-plus', async () => {
  const actual = await vi.importActual('element-plus')
  return {
    ...actual,
    ElMessage: {
      success: vi.fn(),
      error: vi.fn(),
      warning: vi.fn(),
      info: vi.fn(),
    },
    ElMessageBox: { confirm: vi.fn(() => Promise.resolve()) },
  }
})

import UserManagementPage from '@/views/UserManagementPage.vue'
import { useUserStore } from '@/stores/user'

const targetUser = {
  id: 2,
  username: 'inspector_a',
  email: 'inspector@example.com',
  avatar: null,
  phone: null,
  is_active: true,
  is_superuser: false,
  roles: ['quality_inspector'],
  permissions: ['detection:execute'],
  last_login_at: '2026-07-17T08:00:00',
  created_at: '2026-07-16T08:00:00',
}

function mountPage() {
  const pinia = createPinia()
  setActivePinia(pinia)
  const userStore = useUserStore()
  userStore.user = {
    id: 1,
    username: 'admin',
    permissions: ['user:manage'],
    is_superuser: false,
  }

  return shallowMount(UserManagementPage, {
    global: {
      plugins: [pinia, ElementPlus],
      directives: { loading: () => {} },
      stubs: {
        ElTable: { template: '<div><slot /></div>' },
        ElTableColumn: { template: '<div />' },
      },
    },
  })
}

describe('UserManagementPage', () => {
  beforeEach(() => {
    getUserList.mockReset().mockResolvedValue({
      total: 1,
      page: 1,
      page_size: 20,
      total_pages: 1,
      items: [{ ...targetUser }],
    })
    getRoleList.mockReset().mockResolvedValue({
      roles: [
        {
          name: 'quality_inspector',
          display_name: '普通质检人员',
          description: '执行检测并查看本人历史',
          permissions: ['detection:execute'],
        },
        {
          name: 'production_manager',
          display_name: '生产管理人员',
          description: '查看全厂历史和数据看板',
          permissions: ['dashboard:read:any'],
        },
        {
          name: 'system_admin',
          display_name: '系统管理员',
          description: '拥有全部平台权限',
          permissions: ['user:manage'],
        },
      ],
    })
    updateUserRoles.mockReset().mockResolvedValue({
      ...targetUser,
      roles: ['quality_inspector'],
    })
    updateUserStatus.mockReset().mockResolvedValue({
      ...targetUser,
      is_active: false,
    })
    routerReplace.mockReset()
  })

  it('加载用户及角色信息', async () => {
    const wrapper = mountPage()
    await flushPromises()

    expect(getUserList).toHaveBeenCalledWith({
      page: 1,
      page_size: 20,
      keyword: undefined,
    })
    expect(getRoleList).toHaveBeenCalled()
    expect(wrapper.vm.users[0].username).toBe('inspector_a')
    expect(wrapper.vm.roles[0].display_name).toBe('普通质检人员')
    expect(wrapper.vm.pagination.total).toBe(1)
  })

  it('通过角色对话框保存用户角色', async () => {
    const wrapper = mountPage()
    await flushPromises()

    wrapper.vm.openRoleDialog(wrapper.vm.users[0])
    await wrapper.vm.saveRoles()
    await flushPromises()

    expect(updateUserRoles).toHaveBeenCalledWith(2, ['quality_inspector'])
  })

  it('确认后禁用用户', async () => {
    const wrapper = mountPage()
    await flushPromises()

    const user = wrapper.vm.users[0]
    user.is_active = false
    await wrapper.vm.changeStatus(user)
    await flushPromises()

    expect(updateUserStatus).toHaveBeenCalledWith(2, false)
  })
})
