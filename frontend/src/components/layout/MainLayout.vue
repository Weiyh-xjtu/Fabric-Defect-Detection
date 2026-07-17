<template>
  <div class="main-layout">
    <!-- 顶部导航栏 -->
    <AppHeader />

    <!-- 下方区域：侧边栏 + 内容区 -->
    <div class="layout-body">
      <AppSidebar />

      <!-- 页面内容区 -->
      <main :class="['layout-content', { 'layout-content-chat': isChatRoute }]">
        <router-view />
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
</style>
