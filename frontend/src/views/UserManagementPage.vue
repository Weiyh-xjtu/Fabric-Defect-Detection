<template>
  <div class="user-management-page">
    <header class="page-header">
      <div>
        <h2>用户管理</h2>
        <p>管理平台用户的角色和账号状态，权限变更会立即生效</p>
      </div>
      <el-button :icon="Refresh" :loading="loading" @click="loadUsers">
        刷新
      </el-button>
    </header>

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
  </div>
</template>

<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import { Edit, Refresh, Search } from '@element-plus/icons-vue'
import { ElMessage, ElMessageBox } from 'element-plus'

import {
  getRoleList,
  getUserList,
  updateUserRoles,
  updateUserStatus,
} from '@/api/user'
import { useUserStore } from '@/stores/user'

const router = useRouter()
const userStore = useUserStore()

const users = ref([])
const roles = ref([])
const keyword = ref('')
const loading = ref(false)
const statusUpdatingId = ref(null)
const pagination = reactive({ page: 1, pageSize: 20, total: 0 })

const roleDialogVisible = ref(false)
const roleSaving = ref(false)
const editingUser = ref(null)
const selectedRoleNames = ref([])

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
  const result = await getRoleList()
  roles.value = result.roles || []
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
    await Promise.all([loadRoles(), loadUsers()])
  } catch (error) {
    console.error('[用户管理页面初始化失败]', error)
  }
})

defineExpose({
  users,
  roles,
  pagination,
  loadUsers,
  openRoleDialog,
  saveRoles,
  changeStatus,
})
</script>

<style lang="scss" scoped>
.user-management-page {
  display: flex;
  flex-direction: column;
  gap: 16px;
  padding: 24px;
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
