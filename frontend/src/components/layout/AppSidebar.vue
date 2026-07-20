<template>
  <aside
    :class="[
      'app-sidebar',
      {
        'has-chat-history': isChatRoute && !isSidebarCollapsed,
        'is-collapsed': isSidebarCollapsed,
      },
    ]"
  >
    <el-button
      class="sidebar-collapse-button"
      :icon="isSidebarCollapsed ? ArrowRight : ArrowLeft"
      :title="isSidebarCollapsed ? '展开侧边栏' : '收起侧边栏'"
      :aria-label="isSidebarCollapsed ? '展开侧边栏' : '收起侧边栏'"
      @click="isSidebarCollapsed = !isSidebarCollapsed"
    />

    <div class="menu-scroll-area">
      <el-menu
        :default-active="activeMenu"
        :router="true"
        :collapse="isSidebarCollapsed"
        :collapse-transition="false"
        background-color="transparent"
        text-color="#3f4a5f"
        active-text-color="#3f4a5f"
      >
        <el-menu-item
          v-for="item in menuItems"
          :key="item.path"
          :index="item.path"
          :title="isSidebarCollapsed ? item.title : undefined"
        >
          <el-icon>
            <component :is="item.icon" />
          </el-icon>
          <span v-if="!isSidebarCollapsed">{{ item.title }}</span>
        </el-menu-item>
      </el-menu>
    </div>

    <section
      v-if="isChatRoute && !isSidebarCollapsed"
      :class="['chat-history-section', { 'is-history-collapsed': isHistoryCollapsed }]"
    >
      <div class="history-header">
        <span class="history-title">历史对话</span>
        <div class="history-actions">
          <el-button
            class="history-icon-button history-search-button"
            text
            :icon="Search"
            title="搜索历史对话"
            aria-label="搜索历史对话"
            @click="toggleHistorySearch"
          />
          <el-button
            class="history-icon-button history-toggle-button"
            text
            :icon="isHistoryCollapsed ? ArrowDownBold : ArrowUpBold"
            :title="isHistoryCollapsed ? '展开历史对话' : '收起历史对话'"
            :aria-label="isHistoryCollapsed ? '展开历史对话' : '收起历史对话'"
            @click="toggleHistory"
          />
          <el-button size="small" class="new-chat-button" @click="handleNewChat">
            + 新对话
          </el-button>
        </div>
      </div>

      <div v-if="searchVisible && !isHistoryCollapsed" class="history-search-row">
        <el-input
          v-model="searchQuery"
          class="history-search-input"
          size="small"
          clearable
          :prefix-icon="Search"
          placeholder="按标题搜索对话"
          aria-label="按标题搜索对话"
        />
      </div>

      <div
        v-if="!isHistoryCollapsed"
        v-loading="agentStore.isSessionsLoading"
        class="session-list"
      >
        <div
          v-for="session in filteredSessions"
          :key="session.session_uuid"
          :class="[
            'session-item',
            { active: session.session_uuid === agentStore.currentSessionId },
          ]"
          @click="handleSelectSession(session.session_uuid)"
        >
          <div class="session-info">
            <div class="session-name">{{ session.title || '新对话' }}</div>
            <div class="session-meta">
              {{ session.message_count }} 条 ·
              {{ formatSessionTime(session.last_message_at || session.created_at) }}
            </div>
          </div>
          <div class="session-actions">
            <el-button
              class="session-action session-edit"
              size="small"
              text
              :icon="EditPen"
              title="编辑标题"
              aria-label="编辑标题"
              @click.stop="handleEditSession(session)"
            />
            <el-button
              class="session-action session-delete"
              size="small"
              text
              :icon="Delete"
              title="删除会话"
              aria-label="删除会话"
              @click.stop="handleDeleteSession(session)"
            />
          </div>
        </div>
        <div v-if="!filteredSessions.length" class="session-empty">
          {{ searchQuery.trim() ? '未找到匹配的对话' : '暂无历史对话' }}
        </div>
      </div>
    </section>
  </aside>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  ChatDotRound,
  ArrowLeft,
  ArrowRight,
  Camera,
  Box,
  ArrowDownBold,
  ArrowUpBold,
  Cpu,
  Clock,
  Collection,
  DataAnalysis,
  Delete,
  EditPen,
  Search,
  Setting,
  UserFilled,
} from '@element-plus/icons-vue'
import { useAgentStore } from '@/stores/agent'
import { useUserStore } from '@/stores/user'

const route = useRoute()
const router = useRouter()
const agentStore = useAgentStore()
const userStore = useUserStore()
const isSidebarCollapsed = ref(false)
const isHistoryCollapsed = ref(false)
const searchVisible = ref(false)
const searchQuery = ref('')

/** 当前激活的菜单项 */
const activeMenu = computed(() => {
  return '/' + route.path.split('/')[1]
})

const isChatRoute = computed(() => route.path.startsWith('/chat'))

/** 侧边栏菜单配置 */
const allMenuItems = [
  { path: '/chat', title: '智能对话', icon: ChatDotRound, permission: 'chat:use' },
  { path: '/detection', title: '检测工作台', icon: Camera, permission: 'detection:execute' },
  { path: '/training', title: '模型训练', icon: Cpu, permission: 'model:manage' },
  { path: '/models', title: '模型管理', icon: Box, permission: 'model:manage' },
  { path: '/history', title: '历史记录', icon: Clock, anyPermission: ['history:read:own', 'history:read:any'] },
  { path: '/knowledge', title: '知识库', icon: Collection, permission: 'knowledge:manage' },
  { path: '/dashboard', title: '数据看板', icon: DataAnalysis, permission: 'dashboard:read:any' },
  { path: '/users', title: '用户管理', icon: UserFilled, permission: 'user:manage' },
  { path: '/settings', title: '系统设置', icon: Setting },
]

const menuItems = computed(() => allMenuItems.filter((item) => (
  (!item.permission || userStore.hasPermission(item.permission))
  && (!item.anyPermission || userStore.hasAnyPermission(item.anyPermission))
)))

const filteredSessions = computed(() => {
  const keyword = searchQuery.value.trim().toLocaleLowerCase()
  if (!keyword) return agentStore.sessions

  return agentStore.sessions.filter((session) => (
    (session.title || '新对话').toLocaleLowerCase().includes(keyword)
  ))
})

function toggleHistorySearch() {
  if (isHistoryCollapsed.value) {
    isHistoryCollapsed.value = false
    searchVisible.value = true
    return
  }
  searchVisible.value = !searchVisible.value
  if (!searchVisible.value) {
    searchQuery.value = ''
  }
}

function toggleHistory() {
  isHistoryCollapsed.value = !isHistoryCollapsed.value
}

async function refreshSessions() {
  try {
    await agentStore.fetchSessions()
  } catch (err) {
    console.error('[会话列表加载失败]', err)
  }
}

async function handleSelectSession(sessionUuid) {
  if (sessionUuid === agentStore.currentSessionId || agentStore.isLoading) return
  try {
    if (!isChatRoute.value) {
      await router.push('/chat')
    }
    await agentStore.loadSession(sessionUuid)
  } catch (err) {
    console.error('[会话历史加载失败]', err)
    ElMessage.error('加载会话历史失败')
  }
}

async function handleNewChat() {
  if (!isChatRoute.value) {
    await router.push('/chat')
  }
  agentStore.newChat()
}

async function handleEditSession(session) {
  let result
  try {
    result = await ElMessageBox.prompt('请输入新的对话标题', '编辑标题', {
      inputValue: session.title || '新对话',
      inputPlaceholder: '请输入对话标题',
      confirmButtonText: '保存',
      cancelButtonText: '取消',
      inputValidator(value) {
        const title = (value || '').trim()
        if (!title) return '标题不能为空'
        if (title.length > 200) return '标题不能超过 200 个字符'
        return true
      },
    })
  } catch {
    return
  }

  const title = result.value.trim()
  if (title === session.title) return

  try {
    await agentStore.renameSession(session.session_uuid, title)
    ElMessage.success('标题已更新')
  } catch (err) {
    console.error('[会话标题更新失败]', err)
    ElMessage.error('标题更新失败')
  }
}

async function handleDeleteSession(session) {
  try {
    await ElMessageBox.confirm(
      `确定删除会话「${session.title || '新对话'}」吗？此操作不可恢复。`,
      '删除会话',
      {
        type: 'warning',
        confirmButtonText: '删除',
        cancelButtonText: '取消',
      },
    )
  } catch {
    return
  }

  try {
    await agentStore.removeSession(session.session_uuid)
    ElMessage.success('会话已删除')
  } catch (err) {
    console.error('[会话删除失败]', err)
    ElMessage.error('删除会话失败')
  }
}

function formatSessionTime(value) {
  if (!value) return ''
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return ''

  const now = new Date()
  const sameDay = date.toDateString() === now.toDateString()

  if (sameDay) {
    return date.toLocaleTimeString('zh-CN', {
      hour: '2-digit',
      minute: '2-digit',
    })
  }

  return date.toLocaleDateString('zh-CN', {
    month: '2-digit',
    day: '2-digit',
  })
}

onMounted(() => {
  refreshSessions()
})
</script>

<style lang="scss" scoped>
.app-sidebar {
  position: relative;
  display: flex;
  flex-direction: column;
  width: $sidebar-width;
  height: 100%;
  overflow: visible;
  font-size: 14px;
  background:
    radial-gradient(circle at 18px 18px, rgba(47, 58, 79, 0.026) 1px, transparent 1px),
    linear-gradient(145deg, rgba(220, 72, 72, 0.085) 0%, rgba(220, 72, 72, 0.045) 58%, transparent 96%),
    #fff;
  background-size: 24px 24px, 100% 100%, auto;
  border-right: 1px solid #e8ecf3;
  flex-shrink: 0;
  transition: width 0.2s ease;

  .el-menu-item {
    height: 44px;
    margin-bottom: 2px;
    border-radius: 6px;
    font-size: 14px;
    line-height: 44px;
    transition: background 0.15s, color 0.15s;

    &.is-active {
      font-weight: 700;
      background-color: #fff !important;
      box-shadow: inset 3px 0 0 $signal-orange;
    }

    &:hover {
      color: #3f4a5f !important;
      background-color: #fff !important;
    }
  }

  &.is-collapsed {
    width: $sidebar-collapsed-width;

    .menu-scroll-area {
      scrollbar-gutter: auto;
    }

    .menu-scroll-area .el-menu {
      width: auto;
      padding-right: 8px;
      padding-left: 8px;
    }

    .el-menu-item {
      justify-content: center;
      padding: 0 !important;
    }
  }
}

.history-icon-button {
  width: 30px;
  height: 30px;
  padding: 0;
  color: #6b7484;
  border-radius: 6px;

  &:hover,
  &:focus {
    color: $signal-orange;
    background: #fff;
  }
}

.sidebar-collapse-button {
  position: absolute;
  top: 50%;
  right: -1px;
  z-index: 5;
  width: 24px;
  height: 34px;
  padding: 0;
  color: #6b7484;
  background: #fff;
  border-color: #dfe3ea;
  border-left: none;
  border-radius: 0 7px 7px 0;
  box-shadow: 0 2px 6px rgba(47, 58, 79, 0.06);
  transform: translate(100%, -50%);

  &:hover,
  &:focus {
    color: $signal-orange;
    background: #fff;
    border-color: $signal-orange;
  }

  :deep(.el-icon) {
    font-size: 15px;
  }
}

.menu-scroll-area {
  flex: 1 1 auto;
  min-height: 0;
  overflow-y: auto;
  overscroll-behavior: contain;
  scrollbar-gutter: stable;

  .el-menu {
    padding: 10px 8px;
    border-right: none;
    background: transparent;
  }
}

.app-sidebar.has-chat-history .menu-scroll-area {
  flex: 0 0 auto;
  max-height: none;
}

.chat-history-section {
  display: flex;
  flex: 1;
  min-height: 0;
  flex-direction: column;
  border-top: 1px solid #e8ecf3;

  &.is-history-collapsed {
    flex: 0 0 auto;
  }
}

.history-header {
  display: flex;
  flex-shrink: 0;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  padding: 10px 8px 10px 12px;
}

.history-title {
  flex-shrink: 0;
  color: #6b7484;
  font-family: $font-mono;
  font-size: 12px;
  letter-spacing: 0.06em;
  font-weight: 600;
}

.history-actions {
  display: flex;
  min-width: 0;
  align-items: center;
  justify-content: flex-end;
  gap: 2px;

  .el-button + .el-button {
    margin-left: 0;
  }
}

.history-icon-button {
  width: 26px;
  height: 26px;
}

.new-chat-button {
  margin-left: 2px !important;
  color: #3f4a5f;
  font-weight: 600;
  background: #fff;
  border-color: #dcdfe6;
  border-radius: 12px;

  &:hover,
  &:focus {
    color: #3f4a5f;
    background: #fff;
    border-color: $signal-orange;
  }
}

.history-search-row {
  flex-shrink: 0;
  padding: 0 8px 8px;
}

.history-search-input {
  :deep(.el-input__wrapper) {
    border-radius: 6px;
    box-shadow: 0 0 0 1px #dfe3ea inset;

    &.is-focus {
      box-shadow: 0 0 0 1px $signal-orange inset;
    }
  }
}

.session-list {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  overscroll-behavior: contain;
  scrollbar-gutter: stable;
  padding: 0 8px 12px;

  :deep(.el-loading-mask) {
    background-color: rgba(247, 248, 251, 0.75);
  }

  :deep(.el-loading-spinner .path) {
    stroke: $signal-orange;
  }
}

.session-item {
  position: relative;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  margin-bottom: 4px;
  padding: 9px 10px;
  color: #6b7484;
  cursor: pointer;
  border-radius: 6px;
  transition: background 0.2s, color 0.2s;

  &:hover {
    color: #3f4a5f;
    background: #fff;

    .session-actions {
      visibility: visible;
    }
  }

  &.active {
    color: #3f4a5f;
    font-weight: 700;
    background: #fff;
    box-shadow: inset 3px 0 0 $signal-orange;

    .session-actions {
      visibility: visible;
    }
  }
}

.session-info {
  flex: 1;
  min-width: 0;
}

.session-name {
  overflow: hidden;
  font-size: 13px;
  line-height: 19px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.session-meta {
  margin-top: 2px;
  color: #9aa3b2;
  font-family: $font-mono;
  font-size: 11px;
}

.session-actions {
  position: absolute;
  top: 50%;
  right: 6px;
  display: flex;
  align-items: center;
  gap: 0;
  visibility: hidden;
  background: #fff;
  border-radius: 6px;
  box-shadow: -8px 0 12px #fff;
  transform: translateY(-50%);

  .el-button + .el-button {
    margin-left: 0;
  }
}

.session-action {
  width: 26px;
  height: 26px;
  padding: 0;
  color: #9aa3b2;

  &:hover {
    color: $signal-orange;
    background: transparent;
  }
}

.session-empty {
  padding: 12px;
  color: #9aa3b2;
  font-size: 13px;
  text-align: center;
}
</style>
