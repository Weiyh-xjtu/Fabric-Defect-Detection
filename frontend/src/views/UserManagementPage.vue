<template>
  <div class="user-management-page">
    <header class="page-header">
      <div>
        <h2>用户管理</h2>
        <p>管理平台用户、角色和权限，权限变更会立即生效</p>
      </div>
      <el-button :icon="Refresh" :loading="loading || rolesLoading" @click="refreshActiveTab">
        刷新
      </el-button>
    </header>

    <el-tabs v-model="activeTab" class="management-tabs">
      <el-tab-pane label="用户列表" name="users">
    <section class="summary-grid">
      <el-card shadow="never">
        <span class="summary-label">用户总数</span>
        <strong>{{ pagination.total }}</strong>
      </el-card>
      <el-card shadow="never">
        <span class="summary-label">当前页启用</span>
        <strong class="success-text">{{ activeCount }}</strong>
      </el-card>
      <el-card shadow="never">
        <span class="summary-label">当前页管理员</span>
        <strong>{{ adminCount }}</strong>
      </el-card>
    </section>

    <el-card shadow="never" class="filter-card">
      <el-form :inline="true" @submit.prevent="searchUsers">
        <el-form-item label="用户搜索">
          <el-input
            v-model="keyword"
            clearable
            placeholder="用户名或邮箱"
            style="width: 280px"
            @keyup.enter="searchUsers"
            @clear="searchUsers"
          />
        </el-form-item>
        <el-form-item>
          <el-button type="primary" :icon="Search" @click="searchUsers">搜索</el-button>
        </el-form-item>
      </el-form>
    </el-card>

    <el-card shadow="never">
      <el-table v-loading="loading" :data="users" stripe empty-text="暂无用户">
        <el-table-column prop="id" label="ID" width="72" />
        <el-table-column label="用户" min-width="190">
          <template #default="{ row }">
            <div class="user-cell">
              <el-avatar :size="36" :src="row.avatar || undefined">
                {{ row.username?.slice(0, 1).toUpperCase() }}
              </el-avatar>
              <div>
                <div class="username-line">
                  <strong>{{ row.username }}</strong>
                  <el-tag v-if="row.id === userStore.user?.id" size="small" type="info">
                    当前账号
                  </el-tag>
                  <el-tag v-if="row.is_superuser" size="small" type="danger">
                    超级管理员
                  </el-tag>
                </div>
                <span class="secondary-text">{{ row.email }}</span>
              </div>
            </div>
          </template>
        </el-table-column>
        <el-table-column label="角色" min-width="240">
          <template #default="{ row }">
            <el-space wrap>
              <el-tag
                v-for="roleName in row.roles"
                :key="roleName"
                :type="roleTagType(roleName)"
                effect="light"
              >
                {{ roleDisplayName(roleName) }}
              </el-tag>
              <span v-if="!row.roles?.length" class="secondary-text">未分配角色</span>
            </el-space>
          </template>
        </el-table-column>
        <el-table-column label="状态" width="130" align="center">
          <template #default="{ row }">
            <el-switch
              v-model="row.is_active"
              inline-prompt
              active-text="启用"
              inactive-text="禁用"
              :disabled="row.id === userStore.user?.id || statusUpdatingId === row.id"
              :loading="statusUpdatingId === row.id"
              @change="changeStatus(row)"
            />
          </template>
        </el-table-column>
        <el-table-column label="最后登录" min-width="170">
          <template #default="{ row }">{{ formatDate(row.last_login_at) }}</template>
        </el-table-column>
        <el-table-column label="注册时间" min-width="170">
          <template #default="{ row }">{{ formatDate(row.created_at) }}</template>
        </el-table-column>
        <el-table-column label="操作" width="120" fixed="right">
          <template #default="{ row }">
            <el-button link type="primary" :icon="Edit" @click="openRoleDialog(row)">
              分配角色
            </el-button>
          </template>
        </el-table-column>
      </el-table>

      <div class="pagination-wrap">
        <el-pagination
          v-model:current-page="pagination.page"
          v-model:page-size="pagination.pageSize"
          :page-sizes="[10, 20, 50, 100]"
          :total="pagination.total"
          layout="total, sizes, prev, pager, next, jumper"
          @current-change="loadUsers"
          @size-change="handlePageSizeChange"
        />
      </div>
    </el-card>
      </el-tab-pane>

      <el-tab-pane label="角色权限" name="roles">
        <div class="roles-toolbar">
          <el-alert
            type="info"
            :closable="false"
            show-icon
            title="修改角色权限会立即影响该角色下的所有用户；超级管理员不受角色权限限制。"
            class="roles-tip"
          />
          <el-button type="primary" :icon="Plus" @click="openRoleFormDialog(null)">
            新建角色
          </el-button>
        </div>

        <div v-loading="rolesLoading" class="role-grid">
          <el-card v-for="role in roles" :key="role.id ?? role.name" shadow="never" class="role-card">
            <template #header>
              <div class="role-card-header">
                <div class="role-title-block">
                  <strong class="role-display-name">{{ role.display_name }}</strong>
                  <div class="role-title-tags">
                    <el-tag :type="roleTagType(role.name)" size="small" effect="light">
                      {{ role.name }}
                    </el-tag>
                    <el-tag v-if="role.is_system" size="small" type="info" effect="plain">
                      内置
                    </el-tag>
                  </div>
                  <span class="secondary-text">{{ role.description || '暂无描述' }}</span>
                </div>
                <div class="role-card-actions">
                  <el-button link type="primary" :icon="Edit" @click="openRoleFormDialog(role)">
                    编辑
                  </el-button>
                  <el-button link type="primary" :icon="Key" @click="openPermissionDialog(role)">
                    权限
                  </el-button>
                  <el-button
                    v-if="!role.is_system"
                    link
                    type="danger"
                    :icon="Delete"
                    @click="removeRole(role)"
                  >
                    删除
                  </el-button>
                </div>
              </div>
            </template>
            <div class="role-card-body">
              <div class="role-perm-count">
                <span class="secondary-text">已配置权限</span>
                <strong>{{ role.permissions?.length || 0 }}</strong>
                <span class="secondary-text">项</span>
              </div>
              <div v-if="role.permissions?.length" class="perm-module-list">
                <div
                  v-for="group in groupRolePermissions(role)"
                  :key="group.module"
                  class="perm-module"
                >
                  <span class="perm-module-label">{{ moduleLabel(group.module) }}</span>
                  <el-space wrap :size="6">
                    <el-tag
                      v-for="item in group.items"
                      :key="item.code"
                      type="info"
                      effect="plain"
                      size="small"
                    >
                      {{ item.name }}
                    </el-tag>
                  </el-space>
                </div>
              </div>
              <el-empty
                v-else
                description="未配置任何权限"
                :image-size="48"
              />
            </div>
          </el-card>
        </div>
      </el-tab-pane>
    </el-tabs>

    <el-dialog
      v-model="roleDialogVisible"
      title="分配用户角色"
      width="min(560px, 92vw)"
      destroy-on-close
    >
      <div v-if="editingUser" class="dialog-user">
        <strong>{{ editingUser.username }}</strong>
        <span>{{ editingUser.email }}</span>
      </div>

      <el-alert
        v-if="editingUser?.is_superuser"
        type="warning"
        :closable="false"
        title="该账号启用了超级管理员旁路，即使调整角色仍拥有全部权限。"
        show-icon
      />

      <el-checkbox-group v-model="selectedRoleNames" class="role-options">
        <el-checkbox
          v-for="role in roles"
          :key="role.name"
          :value="role.name"
          border
        >
          <div class="role-option-content">
            <strong>{{ role.display_name }}</strong>
            <span>{{ role.description || role.name }}</span>
          </div>
        </el-checkbox>
      </el-checkbox-group>

      <template #footer>
        <el-button @click="roleDialogVisible = false">取消</el-button>
        <el-button
          type="primary"
          :loading="roleSaving"
          :disabled="selectedRoleNames.length === 0"
          @click="saveRoles"
        >
          保存角色
        </el-button>
      </template>
    </el-dialog>

    <el-dialog
      v-model="permissionDialogVisible"
      title="编辑角色权限"
      width="min(680px, 94vw)"
      destroy-on-close
    >
      <div v-if="editingRole" class="dialog-user">
        <strong>{{ editingRole.display_name }}</strong>
        <span>{{ editingRole.description || editingRole.name }}</span>
      </div>

      <div class="permission-modules">
        <section v-for="group in groupedPermissions" :key="group.module">
          <div class="module-header">
            <span class="module-title">{{ moduleLabel(group.module) }}</span>
            <el-button link size="small" @click="toggleModule(group)">
              {{ isModuleAllSelected(group) ? '取消全选' : '全选' }}
            </el-button>
          </div>
          <el-checkbox-group v-model="selectedPermissionCodes" class="permission-options">
            <el-checkbox
              v-for="permission in group.items"
              :key="permission.code"
              :value="permission.code"
              border
            >
              <div class="role-option-content">
                <strong>{{ permission.name }}</strong>
                <span>{{ permission.code }}</span>
              </div>
            </el-checkbox>
          </el-checkbox-group>
        </section>
      </div>

      <template #footer>
        <el-button @click="permissionDialogVisible = false">取消</el-button>
        <el-button
          type="primary"
          :loading="permissionSaving"
          @click="savePermissions"
        >
          保存权限
        </el-button>
      </template>
    </el-dialog>

    <el-dialog
      v-model="roleFormDialogVisible"
      :title="roleFormIsCreate ? '新建角色' : '编辑角色'"
      width="min(520px, 92vw)"
      destroy-on-close
    >
      <el-form label-width="90px" @submit.prevent>
        <el-form-item label="角色标识" required>
          <el-input
            v-model="roleForm.name"
            :disabled="!roleFormIsCreate && roleFormIsSystem"
            placeholder="如 workshop_leader，仅字母数字下划线"
            maxlength="50"
          />
          <div v-if="!roleFormIsCreate && roleFormIsSystem" class="secondary-text form-hint">
            系统内置角色的标识不可修改
          </div>
        </el-form-item>
        <el-form-item label="显示名" required>
          <el-input v-model="roleForm.display_name" placeholder="如 车间班组长" maxlength="100" />
        </el-form-item>
        <el-form-item label="描述">
          <el-input
            v-model="roleForm.description"
            type="textarea"
            :rows="2"
            maxlength="255"
            placeholder="角色职责说明（可选）"
          />
        </el-form-item>
      </el-form>

      <template #footer>
        <el-button @click="roleFormDialogVisible = false">取消</el-button>
        <el-button
          type="primary"
          :loading="roleFormSaving"
          :disabled="!roleForm.name.trim() || !roleForm.display_name.trim()"
          @click="saveRoleForm"
        >
          {{ roleFormIsCreate ? '创建角色' : '保存修改' }}
        </el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import { Delete, Edit, Key, Plus, Refresh, Search } from '@element-plus/icons-vue'
import { ElMessage, ElMessageBox } from 'element-plus'

import {
  createRole,
  deleteRole,
  getPermissionList,
  getRoleList,
  getUserList,
  updateRole,
  updateRolePermissions,
  updateUserRoles,
  updateUserStatus,
} from '@/api/user'
import { useUserStore } from '@/stores/user'

const router = useRouter()
const userStore = useUserStore()

const activeTab = ref('users')
const users = ref([])
const roles = ref([])
const permissions = ref([])
const keyword = ref('')
const loading = ref(false)
const rolesLoading = ref(false)
const statusUpdatingId = ref(null)
const pagination = reactive({ page: 1, pageSize: 20, total: 0 })

const roleDialogVisible = ref(false)
const roleSaving = ref(false)
const editingUser = ref(null)
const selectedRoleNames = ref([])

const permissionDialogVisible = ref(false)
const permissionSaving = ref(false)
const editingRole = ref(null)
const selectedPermissionCodes = ref([])

const roleFormDialogVisible = ref(false)
const roleFormSaving = ref(false)
const roleFormIsCreate = ref(true)
const roleFormIsSystem = ref(false)
const roleFormEditingId = ref(null)
const roleForm = reactive({ name: '', display_name: '', description: '' })

const MODULE_LABELS = {
  agent: '智能对话',
  knowledge: '知识库',
  detection: '目标检测',
  history: '检测历史',
  dashboard: '数据看板',
  training: '模型训练',
  system: '系统管理',
}

const groupedPermissions = computed(() => {
  const groups = new Map()
  for (const permission of permissions.value) {
    const module = permission.module || 'other'
    if (!groups.has(module)) groups.set(module, { module, items: [] })
    groups.get(module).items.push(permission)
  }
  return [...groups.values()]
})

// code -> permission 元信息，用于在角色卡片中按模块分组展示权限
const permissionMetaMap = computed(() => {
  const map = new Map()
  for (const permission of permissions.value) {
    map.set(permission.code, permission)
  }
  return map
})

// 将某个角色持有的权限码按模块归类，供卡片分组渲染
function groupRolePermissions(role) {
  const groups = new Map()
  for (const code of role.permissions || []) {
    const meta = permissionMetaMap.value.get(code)
    const module = meta?.module || 'other'
    if (!groups.has(module)) groups.set(module, { module, items: [] })
    groups.get(module).items.push({ code, name: meta?.name || code })
  }
  return [...groups.values()]
}

const activeCount = computed(() => users.value.filter((item) => item.is_active).length)
const adminCount = computed(() => users.value.filter((item) => (
  item.is_superuser || item.roles?.includes('system_admin')
)).length)

function roleDisplayName(roleName) {
  return roles.value.find((item) => item.name === roleName)?.display_name || roleName
}

function roleTagType(roleName) {
  return {
    system_admin: 'danger',
    production_manager: 'warning',
    quality_inspector: 'success',
  }[roleName] || 'info'
}

function formatDate(value) {
  return value ? new Date(value).toLocaleString('zh-CN', { hour12: false }) : '-'
}

async function loadRoles() {
  rolesLoading.value = true
  try {
    const result = await getRoleList()
    roles.value = result.roles || []
  } finally {
    rolesLoading.value = false
  }
}

async function loadPermissions() {
  const result = await getPermissionList()
  permissions.value = result.permissions || []
}

function refreshActiveTab() {
  if (activeTab.value === 'roles') {
    void loadRoles()
    void loadPermissions()
  } else {
    void loadUsers()
  }
}

function permissionName(code) {
  return permissions.value.find((item) => item.code === code)?.name || code
}

function moduleLabel(module) {
  return MODULE_LABELS[module] || module
}

function isModuleAllSelected(group) {
  return group.items.every((item) => selectedPermissionCodes.value.includes(item.code))
}

function toggleModule(group) {
  const codes = group.items.map((item) => item.code)
  if (isModuleAllSelected(group)) {
    selectedPermissionCodes.value = selectedPermissionCodes.value.filter(
      (code) => !codes.includes(code),
    )
  } else {
    selectedPermissionCodes.value = [
      ...new Set([...selectedPermissionCodes.value, ...codes]),
    ]
  }
}

function openPermissionDialog(role) {
  editingRole.value = role
  selectedPermissionCodes.value = [...(role.permissions || [])]
  permissionDialogVisible.value = true
}

async function savePermissions() {
  if (!editingRole.value) return
  permissionSaving.value = true
  try {
    const updated = await updateRolePermissions(
      editingRole.value.id,
      selectedPermissionCodes.value,
    )
    Object.assign(editingRole.value, updated)
    permissionDialogVisible.value = false
    ElMessage.success('角色权限已更新')

    // 若当前账号持有该角色，其权限可能已变化，刷新后按需跳转
    if (userStore.roles?.includes(updated.name)) {
      await userStore.fetchUserInfo()
      if (!userStore.hasPermission('user:manage')) {
        await router.replace('/forbidden')
      }
    }
  } catch (error) {
    console.error('[角色权限更新失败]', error)
  } finally {
    permissionSaving.value = false
  }
}

function openRoleFormDialog(role) {
  roleFormIsCreate.value = !role
  roleFormIsSystem.value = Boolean(role?.is_system)
  roleFormEditingId.value = role?.id ?? null
  roleForm.name = role?.name || ''
  roleForm.display_name = role?.display_name || ''
  roleForm.description = role?.description || ''
  roleFormDialogVisible.value = true
}

async function saveRoleForm() {
  roleFormSaving.value = true
  try {
    if (roleFormIsCreate.value) {
      const created = await createRole({
        name: roleForm.name.trim(),
        display_name: roleForm.display_name.trim(),
        description: roleForm.description.trim() || null,
        permission_codes: [],
      })
      roles.value = [...roles.value, created]
      ElMessage.success('角色已创建，可继续为其配置权限')
    } else {
      const updated = await updateRole(roleFormEditingId.value, {
        name: roleForm.name.trim(),
        display_name: roleForm.display_name.trim(),
        description: roleForm.description.trim() || null,
      })
      const target = roles.value.find((item) => item.id === roleFormEditingId.value)
      if (target) Object.assign(target, updated)
      ElMessage.success('角色信息已更新')
    }
    roleFormDialogVisible.value = false
    await loadUsers()
  } catch (error) {
    console.error('[角色保存失败]', error)
  } finally {
    roleFormSaving.value = false
  }
}

async function removeRole(role) {
  try {
    await ElMessageBox.confirm(
      `确定删除角色「${role.display_name}」吗？该角色下的用户将失去此角色及其权限。`,
      '删除角色',
      { type: 'warning', confirmButtonText: '删除', cancelButtonText: '取消' },
    )
  } catch {
    return
  }

  try {
    const result = await deleteRole(role.id)
    roles.value = roles.value.filter((item) => item.id !== role.id)
    ElMessage.success(
      result.affected_users
        ? `角色已删除，${result.affected_users} 个用户已解除该角色`
        : '角色已删除',
    )
    await loadUsers()
  } catch (error) {
    console.error('[角色删除失败]', error)
  }
}

async function loadUsers() {
  loading.value = true
  try {
    const result = await getUserList({
      page: pagination.page,
      page_size: pagination.pageSize,
      keyword: keyword.value.trim() || undefined,
    })
    users.value = result.items || []
    pagination.total = result.total || 0
  } catch (error) {
    console.error('[用户列表加载失败]', error)
  } finally {
    loading.value = false
  }
}

function searchUsers() {
  pagination.page = 1
  void loadUsers()
}

function handlePageSizeChange() {
  pagination.page = 1
  void loadUsers()
}

function openRoleDialog(user) {
  editingUser.value = user
  selectedRoleNames.value = [...(user.roles || [])]
  roleDialogVisible.value = true
}

async function saveRoles() {
  if (!editingUser.value || selectedRoleNames.value.length === 0) return
  roleSaving.value = true
  try {
    const updated = await updateUserRoles(
      editingUser.value.id,
      selectedRoleNames.value,
    )
    Object.assign(editingUser.value, updated)
    roleDialogVisible.value = false
    ElMessage.success('用户角色已更新')

    if (updated.id === userStore.user?.id) {
      await userStore.fetchUserInfo()
      if (!userStore.hasPermission('user:manage')) {
        await router.replace('/forbidden')
      }
    }
  } catch (error) {
    console.error('[角色更新失败]', error)
  } finally {
    roleSaving.value = false
  }
}

async function changeStatus(user) {
  const nextStatus = user.is_active
  const action = nextStatus ? '启用' : '禁用'
  try {
    await ElMessageBox.confirm(
      `确定${action}用户「${user.username}」吗？${nextStatus ? '' : '禁用后该用户将立即失去访问权限。'}`,
      `${action}用户`,
      {
        type: nextStatus ? 'info' : 'warning',
        confirmButtonText: action,
        cancelButtonText: '取消',
      },
    )
  } catch {
    user.is_active = !nextStatus
    return
  }

  statusUpdatingId.value = user.id
  try {
    const updated = await updateUserStatus(user.id, nextStatus)
    Object.assign(user, updated)
    ElMessage.success(`用户已${action}`)
  } catch (error) {
    user.is_active = !nextStatus
    console.error('[用户状态更新失败]', error)
  } finally {
    statusUpdatingId.value = null
  }
}

onMounted(async () => {
  try {
    await Promise.all([loadRoles(), loadUsers(), loadPermissions()])
  } catch (error) {
    console.error('[用户管理页面初始化失败]', error)
  }
})

defineExpose({
  users,
  roles,
  permissions,
  pagination,
  activeTab,
  loadUsers,
  openRoleDialog,
  saveRoles,
  changeStatus,
  openPermissionDialog,
  savePermissions,
  selectedPermissionCodes,
  openRoleFormDialog,
  saveRoleForm,
  removeRole,
  roleForm,
})
</script>

<style lang="scss" scoped>
.user-management-page {
  display: flex;
  flex-direction: column;
  gap: 16px;
  padding: 20px;
}

.page-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
}

.page-header h2 {
  margin: 0 0 6px;
}

.page-header p,
.secondary-text,
.dialog-user span,
.role-option-content span {
  color: #909399;
}

.page-header p {
  margin: 0;
}

.summary-grid {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 16px;
}

.summary-grid :deep(.el-card__body) {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
}

.summary-grid strong {
  font-size: 28px;
}

.summary-label {
  color: #606266;
}

.success-text {
  color: #67c23a;
}

.filter-card :deep(.el-card__body) {
  padding-bottom: 2px;
}

.user-cell,
.username-line,
.dialog-user {
  display: flex;
  align-items: center;
  gap: 10px;
}

// 长邮箱不得挤压头像：头像固定尺寸，文本区可收缩并截断
.user-cell {
  min-width: 0;

  .el-avatar {
    flex-shrink: 0;
  }

  > div {
    min-width: 0;
  }

  .secondary-text {
    display: block;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
}

.username-line {
  flex-wrap: wrap;
  margin-bottom: 4px;
}

.pagination-wrap {
  display: flex;
  justify-content: flex-end;
  padding-top: 20px;
}

.management-tabs :deep(.el-tabs__content) {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.management-tabs :deep(.el-tab-pane) {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.roles-tip {
  flex-shrink: 0;
  flex: 1;
}

.roles-toolbar {
  display: flex;
  align-items: center;
  gap: 12px;
}

.role-card-actions {
  display: flex;
  align-items: center;
  flex-shrink: 0;
}

.form-hint {
  font-size: 12px;
  line-height: 1.6;
}

.role-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(340px, 1fr));
  gap: 16px;
  min-height: 120px;
}

.role-card {
  transition: box-shadow 0.2s ease, transform 0.2s ease;

  &:hover {
    box-shadow: $shadow-md;
    transform: translateY(-2px);
  }

  :deep(.el-card__header) {
    border-bottom: 1px solid $indigo-line;
    background: linear-gradient(180deg, rgba(63, 77, 104, 0.03), transparent);
  }
}

.role-card-header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
}

.role-title-block {
  display: flex;
  flex-direction: column;
  gap: 4px;
  min-width: 0;

  .role-display-name {
    font-family: $font-display;
    font-size: 15px;
    color: $text-primary;
  }
}

.role-title-tags {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 8px;

  :deep(.el-tag) {
    font-family: $font-mono;
  }
}

.role-card-body {
  display: flex;
  flex-direction: column;
  gap: 14px;
}

.role-perm-count {
  display: flex;
  align-items: baseline;
  gap: 6px;

  strong {
    font-family: $font-mono;
    font-size: 22px;
    color: $signal-orange;
    line-height: 1;
  }
}

.perm-module-list {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.perm-module {
  display: grid;
  grid-template-columns: 76px 1fr;
  gap: 10px;
  align-items: start;
}

.perm-module-label {
  font-size: 12px;
  font-weight: 600;
  color: $text-regular;
  padding-top: 3px;
  position: relative;
  padding-left: 10px;

  &::before {
    content: '';
    position: absolute;
    left: 0;
    top: 5px;
    width: 3px;
    height: 12px;
    background: $signal-orange;
    border-radius: 2px;
  }
}

.role-card-body :deep(.el-empty) {
  padding: 8px 0;
}

.permission-modules {
  display: flex;
  flex-direction: column;
  gap: 20px;
  margin-top: 8px;
  max-height: 55vh;
  overflow-y: auto;
}

.module-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 10px;
}

.module-title {
  font-weight: 600;
  color: #606266;
}

.permission-options {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(260px, 1fr));
  gap: 10px;
}

.permission-options :deep(.el-checkbox) {
  width: 100%;
  height: auto;
  margin: 0;
  padding: 10px 14px;
}

.dialog-user {
  align-items: baseline;
  margin-bottom: 16px;
}

.role-options {
  display: grid;
  gap: 12px;
  margin-top: 16px;
}

.role-options :deep(.el-checkbox) {
  width: 100%;
  height: auto;
  margin: 0;
  padding: 12px 16px;
}

.role-option-content {
  display: flex;
  flex-direction: column;
  gap: 4px;
  white-space: normal;
}

@media (max-width: 768px) {
  .user-management-page {
    padding: 16px;
  }

  .page-header {
    align-items: flex-start;
  }

  .summary-grid {
    grid-template-columns: 1fr;
  }
}
</style>
