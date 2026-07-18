<template>
  <header class="app-header">
    <!-- 左侧：检测框角标包裹的字标 -->
    <div class="header-left">
      <span class="brand-mark">
        <span class="brand-name">FIRESIGHT</span>
      </span>
      <span class="brand-tagline">布面缺陷检测平台</span>
    </div>

    <!-- 右侧：当前场景 + 用户信息 + 退出按钮 -->
    <div class="header-right">
      <!-- 智能对话/检测工作台：显示当前检测场景（全局默认模型归属场景） -->
      <el-tooltip
        v-if="showScene && currentScene"
        :content="`当前全局模型 ${currentModelLabel}，检测与统计均在此场景内进行`"
        placement="bottom"
      >
        <span class="scene-chip">
          <span class="scene-chip-dot" />
          <span class="scene-chip-label">当前场景</span>
          <span class="scene-chip-divider" />
          <span class="scene-chip-name">{{ currentScene.display_name }}</span>
        </span>
      </el-tooltip>
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
import { computed, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ArrowDown, User, SwitchButton } from '@element-plus/icons-vue'
import { ElMessageBox } from 'element-plus'
import { getCurrentScene } from '@/api/detection'
import { useUserStore } from '@/stores/user'
import { useAgentStore } from '@/stores/agent'

const route = useRoute()
const router = useRouter()
const userStore = useUserStore()
const agentStore = useAgentStore()

// 仅在智能对话与检测工作台显示场景胶囊。
const SCENE_ROUTES = ['/chat', '/detection']
const showScene = computed(() => SCENE_ROUTES.includes(route.path))

const currentScene = ref(null)
const currentModelLabel = ref('')

/** 拉取全局默认模型归属场景；失败静默（无模型/无权限时不显示胶囊）。 */
async function loadCurrentScene() {
  try {
    const result = await getCurrentScene()
    currentScene.value = result.scene || null
    currentModelLabel.value = result.scene
      ? `${result.model_name || ''} ${result.model_version || ''}`.trim()
      : ''
  } catch {
    currentScene.value = null
  }
}

// 每次进入智能对话/检测工作台都重新拉取，保证切换全局模型后场景跟随。
watch(
  showScene,
  (visible) => {
    if (visible) loadCurrentScene()
  },
  { immediate: true },
)

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

// 当前检测场景胶囊：中性描边 + 朱橙状态点，弱化存在感、与右侧用户区同灰度语言
.scene-chip {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  padding: 5px 12px;
  border: 1px solid #e8ecf3;
  border-radius: 999px;
  background: #fff;
  cursor: default;
  line-height: 1;
}

.scene-chip-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: $signal-orange;
  box-shadow: 0 0 0 3px rgba(223, 107, 78, 0.14);
  flex-shrink: 0;
}

.scene-chip-label {
  font-size: 12px;
  color: $text-regular;
}

.scene-chip-divider {
  width: 1px;
  height: 10px;
  background: #e8ecf3;
}

.scene-chip-name {
  font-size: 12px;
  font-weight: 600;
  color: $text-regular;
}

.header-right {
  display: flex;
  align-items: center;
  gap: $spacing-md;
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
