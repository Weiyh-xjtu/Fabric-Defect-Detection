<template>
  <header class="app-header">
    <!-- 左侧：Logo + 平台名称 -->
    <div class="header-left">
      <img :src="productAvatar" alt="logo" class="header-logo" />
      <span class="header-title">FIRESIGHT</span>
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
import productAvatar from '@/assets/avatar.png'
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
  background: #fff;
  border-bottom: 1px solid #ebeef5;
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0 $spacing-lg;
  box-shadow: $shadow-sm;
  z-index: 100;
}

.header-left {
  display: flex;
  align-items: center;
  gap: $spacing-sm;
}

.header-logo {
  width: 34px;
  height: 34px;
  object-fit: cover;
  border-radius: 10px;
}

.header-title {
  font-size: 17px;
  font-weight: 700;
  color: #b71c1c;
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
    background: #f5f7fa;
  }
}

.username {
  font-size: 14px;
  color: $text-primary;
}
</style>
