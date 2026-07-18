<template>
  <aside :class="['app-sidebar', { 'has-chat-history': isChatRoute }]">
    <div class="menu-scroll-area">
      <el-menu
        :default-active="activeMenu"
        :router="true"
        background-color="transparent"
        text-color="#aeb8d0"
        active-text-color="#ffffff"
      >
        <el-menu-item
          v-for="item in menuItems"
          :key="item.path"
          :index="item.path"
        >
          <el-icon>
            <component :is="item.icon" />
          </el-icon>
          <span>{{ item.title }}</span>
        </el-menu-item>
      </el-menu>
    </div>

    <section v-if="isChatRoute" class="chat-history-section">
      <div class="history-header">
        <span class="history-title">历史对话</span>
        <el-button size="small" class="new-chat-button" @click="handleNewChat">
          + 新对话
        </el-button>
      </div>

      <div v-loading="agentStore.isSessionsLoading" class="session-list">
        <div
          v-for="session in agentStore.sessions"
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
          <el-button
            class="session-delete"
            size="small"
            text
            @click.stop="handleDeleteSession(session)"
          >
            删除
          </el-button>
        </div>
        <div v-if="!agentStore.sessions.length" class="session-empty">
          暂无历史对话
        </div>
      </div>
    </section>
  </aside>
</template>

<script setup>
import { computed, onMounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  ChatDotRound,
  Camera,
  Box,
  Cpu,
  Clock,
  Collection,
  DataAnalysis,
  Setting,
  UserFilled,
} from '@element-plus/icons-vue'
import { useAgentStore } from '@/stores/agent'
import { useUserStore } from '@/stores/user'

const route = useRoute()
const router = useRouter()
const agentStore = useAgentStore()
const userStore = useUserStore()

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
  display: flex;
  flex-direction: column;
  width: $sidebar-width;
  height: 100%;
  overflow: hidden;
  font-size: 14px;
  background: $indigo-deep;
  @include weave-grid; // 签名纹理：经纬织物网格
  border-right: 1px solid $indigo-line;

  .el-menu-item {
    height: 44px;
    margin-bottom: 2px;
    border-radius: 6px;
    font-size: 14px;
    line-height: 44px;
    transition: background 0.15s, color 0.15s;

    &.is-active {
      font-weight: 600;
      background-color: $indigo-mid !important;
      // 签名元素：激活项左侧的检测框角标线
      box-shadow: inset 3px 0 0 $signal-orange;
    }

    &:hover {
      background-color: $indigo-mid !important;
      color: #fff !important;
    }
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
  flex: 0 1 auto;
  max-height: 52%;
}

.chat-history-section {
  display: flex;
  flex: 1;
  min-height: 0;
  flex-direction: column;
  border-top: 1px solid $indigo-line;
}

.history-header {
  display: flex;
  flex-shrink: 0;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  padding: 12px;
}

.history-title {
  color: $sidebar-text;
  font-family: $font-mono;
  font-size: 12px;
  letter-spacing: 0.06em;
  font-weight: 600;
}

.new-chat-button {
  color: #fff;
  font-weight: 600;
  background: transparent;
  border-color: $indigo-line;

  &:hover,
  &:focus {
    color: #fff;
    background: $indigo-mid;
    border-color: $signal-orange;
  }
}

.session-list {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  overscroll-behavior: contain;
  scrollbar-gutter: stable;
  padding: 0 8px 12px;
}

.session-item {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  margin-bottom: 4px;
  padding: 9px 10px;
  color: $sidebar-text;
  cursor: pointer;
  border-radius: 6px;
  transition: background 0.2s, color 0.2s;

  &:hover {
    background: $indigo-mid;
    color: #dfe5f2;

    .session-delete {
      visibility: visible;
    }
  }

  &.active {
    color: #fff;
    font-weight: 600;
    background: $indigo-mid;
    box-shadow: inset 3px 0 0 $signal-orange;
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
  color: #7a86a3;
  font-family: $font-mono;
  font-size: 11px;
}

.session-delete {
  flex-shrink: 0;
  visibility: hidden;
  color: #7a86a3;

  &:hover {
    color: #fff;
  }
}

.session-empty {
  padding: 12px;
  color: #7a86a3;
  font-size: 13px;
  text-align: center;
}
</style>
