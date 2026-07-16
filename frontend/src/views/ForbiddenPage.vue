<template>
  <main class="forbidden-page">
    <el-result
      icon="warning"
      title="403"
      sub-title="当前账号没有访问该页面的权限"
    >
      <template #extra>
        <el-button type="primary" @click="router.push('/chat')">返回智能对话</el-button>
      </template>
    </el-result>
  </main>
</template>

<script setup>
import { useRouter } from 'vue-router'

const router = useRouter()
</script>

<style scoped>
.forbidden-page {
  display: grid;
  min-height: calc(100vh - 64px);
  place-items: center;
}
</style>
