<template>
  <header class="app-header">
    <!-- 左侧：检测框角标包裹的字标 -->
    <div class="header-left">
      <span class="brand-mark">
        <span class="brand-name">FIRESIGHT</span>
      </span>
      <span class="brand-tagline">布面缺陷检测平台</span>
    </div>

    <!-- 右侧：用户信息 + 退出按钮 -->
    <div class="header-right">
      <el-dropdown trigger="click" @command="handleCommand">
        <div class="user-info">
          <el-avatar :size="32" :src="userStore.avatar || undefined">
            {{ userStore.username?.charAt(0)?.toUpperCase() }}
          </el-avatar>
          <span class="username">{{ userStore.username }}</span>
          <el-icon><ArrowDown /></el-icon>
        </div>
        <template #dropdown>
          <el-dropdown-menu>
            <el-dropdown-item command="profile">
              <el-icon><User /></el-icon>个人中心
            </el-dropdown-item>
            <el-dropdown-item command="logout" divided>
              <el-icon><SwitchButton /></el-icon>退出登录
            </el-dropdown-item>
          </el-dropdown-menu>
        </template>
      </el-dropdown>
    </div>
  </header>
</template>

<script setup>
import { useRouter } from 'vue-router'
import { ArrowDown, User, SwitchButton } from '@element-plus/icons-vue'
import { ElMessageBox } from 'element-plus'
import { useUserStore } from '@/stores/user'
import { useAgentStore } from '@/stores/agent'

const router = useRouter()
const userStore = useUserStore()
const agentStore = useAgentStore()

/** 处理下拉菜单命令 */
function handleCommand(command) {
  switch (command) {
    case 'profile':
      router.push('/settings')
      break
    case 'logout':
      ElMessageBox.confirm('确定要退出登录吗？', '提示', {
        confirmButtonText: '确定',
        cancelButtonText: '取消',
        type: 'warning',
      }).then(() => {
        userStore.logout()
        agentStore.clear()
        router.push('/login')
      }).catch(() => {})
      break
  }
}
</script>

<style lang="scss" scoped>
.app-header {
  height: $header-height;
  background:
    radial-gradient(circle at 18px 18px, rgba(47, 58, 79, 0.028) 1px, transparent 1px),
    linear-gradient(135deg, rgba(223, 107, 78, 0.038) 0%, rgba(223, 107, 78, 0.024) 48%, transparent 82%),
    #fff;
  background-size: 24px 24px, 100% 100%, auto;
  border-bottom: 1px solid #e8ecf3;
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0 $spacing-lg;
  z-index: 100;
}

.header-left {
  display: flex;
  align-items: baseline;
  gap: 12px;
}

// 签名元素：字标嵌在 bbox 检测框角标内
.brand-mark {
  @include bbox-corners($signal-orange, 7px, 2px);
  padding: 4px 10px;
  display: inline-flex;
}

.brand-name {
  font-family: $font-display;
  font-size: 18px;
  font-weight: 700;
  letter-spacing: 0.18em;
  color: $text-primary;
  line-height: 1;
}

.brand-tagline {
  font-family: $font-mono;
  font-size: 11px;
  letter-spacing: 0.08em;
  color: $text-regular;
}

.header-right {
  display: flex;
  align-items: center;
}

.user-info {
  display: flex;
  align-items: center;
  gap: $spacing-sm;
  cursor: pointer;
  padding: 4px 8px;
  border-radius: $border-radius-sm;
  transition: background 0.2s;

  &:hover {
    background: #f7f8fb;
  }
}

.username {
  font-size: 14px;
  color: $text-primary;
}

.user-info :deep(.el-icon) {
  color: $text-regular;
}
</style>
