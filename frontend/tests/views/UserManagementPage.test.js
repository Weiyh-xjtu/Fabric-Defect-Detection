import { beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, shallowMount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import ElementPlus from 'element-plus'

const getUserList = vi.fn()
const getRoleList = vi.fn()
const getPermissionList = vi.fn()
const updateUserRoles = vi.fn()
const updateUserStatus = vi.fn()
const updateRolePermissions = vi.fn()
const createRoleMock = vi.fn()
const updateRoleMock = vi.fn()
const deleteRoleMock = vi.fn()
const routerReplace = vi.fn()

vi.mock('@/api/user', () => ({
  getUserList: (...args) => getUserList(...args),
  getRoleList: (...args) => getRoleList(...args),
  getPermissionList: (...args) => getPermissionList(...args),
  updateUserRoles: (...args) => updateUserRoles(...args),
  updateUserStatus: (...args) => updateUserStatus(...args),
  updateRolePermissions: (...args) => updateRolePermissions(...args),
  createRole: (...args) => createRoleMock(...args),
  updateRole: (...args) => updateRoleMock(...args),
  deleteRole: (...args) => deleteRoleMock(...args),
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
          id: 1,
          name: 'quality_inspector',
          display_name: '普通质检人员',
          description: '执行检测并查看本人历史',
          permissions: ['detection:execute'],
        },
        {
          id: 2,
          name: 'production_manager',
          display_name: '生产管理人员',
          description: '查看全厂历史和数据看板',
          permissions: ['dashboard:read:any'],
        },
        {
          id: 3,
          name: 'system_admin',
          display_name: '系统管理员',
          description: '拥有全部平台权限',
          permissions: ['user:manage'],
        },
      ],
    })
    getPermissionList.mockReset().mockResolvedValue({
      permissions: [
        { id: 1, code: 'detection:execute', name: '执行目标检测', module: 'detection' },
        { id: 2, code: 'dashboard:read:any', name: '查看全厂数据看板', module: 'dashboard' },
        { id: 3, code: 'user:manage', name: '管理用户与角色', module: 'system' },
      ],
    })
    updateRolePermissions.mockReset().mockImplementation((roleId, codes) =>
      Promise.resolve({
        id: roleId,
        name: 'quality_inspector',
        display_name: '普通质检人员',
        description: '执行检测并查看本人历史',
        is_system: true,
        permissions: codes,
      }),
    )
    createRoleMock.mockReset().mockImplementation((payload) =>
      Promise.resolve({
        id: 10,
        name: payload.name,
        display_name: payload.display_name,
        description: payload.description,
        is_system: false,
        permissions: [],
      }),
    )
    updateRoleMock.mockReset().mockImplementation((roleId, payload) =>
      Promise.resolve({
        id: roleId,
        name: payload.name,
        display_name: payload.display_name,
        description: payload.description,
        is_system: false,
        permissions: [],
      }),
    )
    deleteRoleMock.mockReset().mockResolvedValue({ deleted: true, affected_users: 1 })
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

  it('加载权限定义', async () => {
    const wrapper = mountPage()
    await flushPromises()

    expect(getPermissionList).toHaveBeenCalled()
    expect(wrapper.vm.permissions).toHaveLength(3)
  })

  it('通过权限对话框保存角色权限', async () => {
    const wrapper = mountPage()
    await flushPromises()

    const role = wrapper.vm.roles[0]
    wrapper.vm.openPermissionDialog(role)
    wrapper.vm.selectedPermissionCodes = ['detection:execute', 'dashboard:read:any']
    await wrapper.vm.savePermissions()
    await flushPromises()

    expect(updateRolePermissions).toHaveBeenCalledWith(1, [
      'detection:execute',
      'dashboard:read:any',
    ])
    expect(role.permissions).toEqual(['detection:execute', 'dashboard:read:any'])
  })

  it('修改当前账号所属角色权限后刷新用户信息', async () => {
    const wrapper = mountPage()
    await flushPromises()

    const userStore = useUserStore()
    userStore.user.roles = ['quality_inspector']
    userStore.fetchUserInfo = vi.fn().mockResolvedValue()

    wrapper.vm.openPermissionDialog(wrapper.vm.roles[0])
    await wrapper.vm.savePermissions()
    await flushPromises()

    expect(userStore.fetchUserInfo).toHaveBeenCalled()
  })

  it('创建自定义角色', async () => {
    const wrapper = mountPage()
    await flushPromises()

    wrapper.vm.openRoleFormDialog(null)
    wrapper.vm.roleForm.name = 'workshop_leader'
    wrapper.vm.roleForm.display_name = '车间班组长'
    wrapper.vm.roleForm.description = '负责车间调度'
    await wrapper.vm.saveRoleForm()
    await flushPromises()

    expect(createRoleMock).toHaveBeenCalledWith({
      name: 'workshop_leader',
      display_name: '车间班组长',
      description: '负责车间调度',
      permission_codes: [],
    })
    expect(wrapper.vm.roles.some((item) => item.name === 'workshop_leader')).toBe(true)
  })

  it('重命名角色', async () => {
    const wrapper = mountPage()
    await flushPromises()

    const role = { ...wrapper.vm.roles[0], is_system: false }
    wrapper.vm.openRoleFormDialog(role)
    wrapper.vm.roleForm.display_name = '高级质检员'
    await wrapper.vm.saveRoleForm()
    await flushPromises()

    expect(updateRoleMock).toHaveBeenCalledWith(role.id, {
      name: role.name,
      display_name: '高级质检员',
      description: role.description,
    })
  })

  it('确认后删除自定义角色', async () => {
    const wrapper = mountPage()
    await flushPromises()

    const role = { id: 10, name: 'custom_role', display_name: '自定义角色', is_system: false }
    wrapper.vm.roles.push(role)
    await wrapper.vm.removeRole(role)
    await flushPromises()

    expect(deleteRoleMock).toHaveBeenCalledWith(10)
    expect(wrapper.vm.roles.some((item) => item.id === 10)).toBe(false)
  })
})
