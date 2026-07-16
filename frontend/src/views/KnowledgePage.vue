<template>
  <div class="knowledge-page">
    <div class="page-header">
      <div>
        <h2>知识库管理</h2>
        <p>上传 PDF、Markdown、TXT 文档，系统会自动加入知识库并重建向量索引</p>
      </div>
      <el-button type="primary" :loading="rebuilding" @click="triggerRebuild">
        <el-icon><Refresh /></el-icon>立即重建索引
      </el-button>
    </div>

    <!-- ── 索引状态条 ── -->
    <el-alert
      :title="rebuildTitle"
      :type="rebuildAlertType"
      :closable="false"
      show-icon
      class="status-alert"
    >
      <template #default>
        <div class="status-meta">
          <span>检索模式：{{ modeText }}</span>
          <span>文档数：{{ stats.documents ?? '-' }}</span>
          <span>向量块：{{ stats.vector_chunks ?? 0 }}</span>
          <span v-if="rebuild.updated_at">更新于：{{ formatTime(rebuild.updated_at) }}</span>
        </div>
        <div v-if="rebuild.status === 'failed' && rebuild.detail" class="status-detail">
          原因：{{ rebuild.detail }}（词法降级检索仍可用）
        </div>
      </template>
    </el-alert>

    <!-- ── 上传区 ── -->
    <el-card shadow="never" class="upload-card">
      <el-upload
        drag
        multiple
        :auto-upload="true"
        :show-file-list="false"
        accept=".pdf,.md,.txt"
        :http-request="handleUpload"
        :before-upload="beforeUpload"
      >
        <el-icon class="el-icon--upload"><UploadFilled /></el-icon>
        <div class="el-upload__text">拖拽文件到此处，或 <em>点击选择</em></div>
        <template #tip>
          <div class="el-upload__tip">支持 PDF、Markdown（.md）、TXT，单个文件不超过 20MB；同名文件将被覆盖。</div>
        </template>
      </el-upload>
    </el-card>

    <!-- ── 文件列表 ── -->
    <el-card shadow="never" class="file-list-card">
      <template #header>
        <div class="card-header">
          <span>知识库文件（{{ files.length }}）</span>
          <el-button text @click="refreshAll">
            <el-icon><Refresh /></el-icon>刷新
          </el-button>
        </div>
      </template>

      <el-table :data="files" stripe style="width: 100%" v-loading="loading" empty-text="暂无文件">
        <el-table-column prop="name" label="文件名" min-width="240" show-overflow-tooltip />
        <el-table-column label="类型" width="100">
          <template #default="{ row }">
            <el-tag :type="extTagType(row.ext)" size="small">{{ row.ext.replace('.', '').toUpperCase() }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="大小" width="120">
          <template #default="{ row }">{{ formatSize(row.size) }}</template>
        </el-table-column>
        <el-table-column label="修改时间" width="200">
          <template #default="{ row }">{{ formatTime(row.modified_at) }}</template>
        </el-table-column>
        <el-table-column label="操作" width="100" fixed="right">
          <template #default="{ row }">
            <el-button size="small" type="danger" text @click="handleDelete(row)">删除</el-button>
          </template>
        </el-table-column>
      </el-table>
    </el-card>
  </div>
</template>

<script setup>
import { ref, reactive, computed, onMounted, onBeforeUnmount } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Refresh, UploadFilled } from '@element-plus/icons-vue'
import {
  listKnowledgeFiles,
  uploadKnowledgeFiles,
  deleteKnowledgeFile,
  getKnowledgeStats,
  rebuildKnowledge,
} from '@/api/knowledge'

const MAX_FILE_SIZE = 20 * 1024 * 1024
const ALLOWED_EXTS = ['.pdf', '.md', '.txt']

const files = ref([])
const loading = ref(false)
const rebuilding = ref(false)
const stats = reactive({ documents: null, vector_chunks: 0, mode: null })
const rebuild = reactive({ status: 'idle', detail: null, updated_at: null })

let pollTimer = null

const modeText = computed(() => {
  if (stats.mode === 'pgvector') return '向量检索'
  if (stats.mode === 'lexical_fallback') return '词法降级'
  return '-'
})

const rebuildTitle = computed(() => {
  const map = {
    idle: '索引就绪',
    running: '正在重建向量索引…',
    success: '向量索引已是最新',
    failed: '向量索引重建失败',
  }
  return map[rebuild.status] || '索引状态未知'
})

const rebuildAlertType = computed(() => {
  const map = { idle: 'info', running: 'warning', success: 'success', failed: 'error' }
  return map[rebuild.status] || 'info'
})

function extTagType(ext) {
  const map = { '.pdf': 'danger', '.md': 'primary', '.txt': 'info' }
  return map[ext] || 'info'
}

function formatSize(bytes) {
  if (bytes == null) return '-'
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`
}

function formatTime(value) {
  if (!value) return '-'
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? '-' : date.toLocaleString('zh-CN', { hour12: false })
}

async function fetchFiles() {
  loading.value = true
  try {
    const res = await listKnowledgeFiles()
    files.value = res.files || []
  } catch (e) {
    console.error('[知识库文件加载失败]', e)
  } finally {
    loading.value = false
  }
}

async function fetchStats() {
  try {
    const res = await getKnowledgeStats()
    stats.documents = res.documents
    stats.vector_chunks = res.vector_chunks
    stats.mode = res.mode
    applyRebuildState(res.rebuild)
  } catch (e) {
    console.error('[知识库统计加载失败]', e)
  }
}

function applyRebuildState(state) {
  if (!state) return
  rebuild.status = state.status
  rebuild.detail = state.detail
  rebuild.updated_at = state.updated_at
  if (state.status === 'running') {
    startPolling()
  } else {
    stopPolling()
  }
}

async function refreshAll() {
  await Promise.all([fetchFiles(), fetchStats()])
}

/** el-upload 前置校验：拦截非法类型与超大文件，避免无谓请求。 */
function beforeUpload(file) {
  const name = file.name || ''
  const ext = name.slice(name.lastIndexOf('.')).toLowerCase()
  if (!ALLOWED_EXTS.includes(ext)) {
    ElMessage.error(`不支持的文件类型：${name}`)
    return false
  }
  if (file.size > MAX_FILE_SIZE) {
    ElMessage.error(`${name} 超过 20MB 大小限制`)
    return false
  }
  return true
}

/** 自定义上传器：走共享 axios 客户端（自动带 JWT），替代 el-upload 内置 action。 */
async function handleUpload({ file }) {
  const formData = new FormData()
  formData.append('files', file)
  try {
    const res = await uploadKnowledgeFiles(formData)
    ElMessage.success(`${file.name} 上传成功，正在重建索引`)
    applyRebuildState(res.rebuild)
    await fetchFiles()
  } catch (e) {
    console.error('[知识库上传失败]', e)
  }
}

async function handleDelete(row) {
  try {
    await ElMessageBox.confirm(
      `确定删除文件「${row.name}」吗？删除后将自动重建索引。`,
      '删除文件',
      { type: 'warning', confirmButtonText: '删除', cancelButtonText: '取消' },
    )
  } catch {
    return
  }
  try {
    const res = await deleteKnowledgeFile(row.name)
    ElMessage.success('文件已删除，正在重建索引')
    applyRebuildState(res.rebuild)
    await fetchFiles()
  } catch (e) {
    console.error('[知识库删除失败]', e)
  }
}

async function triggerRebuild() {
  rebuilding.value = true
  try {
    const res = await rebuildKnowledge()
    ElMessage.success('已触发重建')
    applyRebuildState(res)
  } catch (e) {
    console.error('[知识库重建失败]', e)
  } finally {
    rebuilding.value = false
  }
}

function startPolling() {
  if (pollTimer) return
  pollTimer = setInterval(fetchStats, 3000)
}

function stopPolling() {
  if (pollTimer) {
    clearInterval(pollTimer)
    pollTimer = null
  }
}

onMounted(refreshAll)
onBeforeUnmount(stopPolling)
</script>

<style scoped>
.knowledge-page {
  padding: 20px;
}

.page-header {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  margin-bottom: 16px;
}

.page-header h2 {
  margin: 0 0 4px;
  font-size: 22px;
}

.page-header p {
  margin: 0;
  color: #909399;
  font-size: 14px;
}

.status-alert {
  margin-bottom: 16px;
}

.status-meta {
  display: flex;
  flex-wrap: wrap;
  gap: 16px;
  font-size: 13px;
}

.status-detail {
  margin-top: 6px;
  font-size: 13px;
}

.upload-card,
.file-list-card {
  margin-bottom: 20px;
}

.card-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
}
</style>
