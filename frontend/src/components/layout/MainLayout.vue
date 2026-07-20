<template>
  <div class="main-layout">
    <!-- 顶部导航栏 -->
    <AppHeader />

    <!-- 下方区域：侧边栏 + 内容区 -->
    <div class="layout-body">
      <AppSidebar />

      <!-- 页面内容区 -->
      <main :class="['layout-content', { 'layout-content-chat': isChatRoute }]">
        <router-view v-slot="{ Component, route: current }">
          <transition name="page-fade" mode="out-in">
            <component :is="Component" :key="current.path" />
          </transition>
        </router-view>
      </main>
    </div>
  </div>
</template>

<script setup>
import { computed } from 'vue'
import { useRoute } from 'vue-router'
import AppHeader from './AppHeader.vue'
import AppSidebar from './AppSidebar.vue'

const route = useRoute()
const isChatRoute = computed(() => route.path.startsWith('/chat'))
</script>

<style lang="scss" scoped>
.main-layout {
  width: 100%;
  height: 100%;
  display: flex;
  flex-direction: column;
}

.layout-body {
  flex: 1;
  display: flex;
  overflow: hidden;
}

.layout-content {
  flex: 1;
  background: $bg-color;
  overflow-y: auto;
  padding: $spacing-lg;
}

.layout-content-chat {
  background: #fff;
  padding: 0;
}

// ── 路由切换过渡：淡入 + 轻微上移 ──────────────────
// 每次进入新页面时内容淡入并向上浮动一点，收尾干脆不拖沓
.page-fade-enter-active {
  transition: opacity 0.28s ease, transform 0.28s ease;
}

.page-fade-leave-active {
  transition: opacity 0.16s ease;
}

.page-fade-enter-from {
  opacity: 0;
  transform: translateY(12px);
}

.page-fade-leave-to {
  opacity: 0;
}

// 尊重系统「减弱动态效果」偏好，关闭位移与淡入
@media (prefers-reduced-motion: reduce) {
  .page-fade-enter-active,
  .page-fade-leave-active {
    transition: none;
  }

  .page-fade-enter-from {
    transform: none;
  }
}
</style>
