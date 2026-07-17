<template>
  <div class="history-page">
    <div class="page-header">
      <div>
        <h2>历史记录</h2>
        <p>查询、筛选和管理当前账号的检测任务</p>
      </div>
      <div class="summary-list">
        <span>全部 <strong>{{ summary.total_tasks }}</strong></span>
        <span>今日 <strong>{{ summary.today_tasks }}</strong></span>
        <span>已完成 <strong>{{ summary.status_counts.completed }}</strong></span>
      </div>
    </div>

    <el-card shadow="never" class="filter-card">
      <el-form :inline="true" :model="filters">
        <el-form-item label="类型">
          <el-select v-model="filters.task_type" clearable placeholder="全部类型" style="width: 130px">
            <el-option v-for="item in typeOptions" :key="item.value" :label="item.label" :value="item.value" />
          </el-select>
        </el-form-item>
        <el-form-item label="状态">
          <el-select v-model="filters.status" clearable placeholder="全部状态" style="width: 130px">
            <el-option v-for="item in statusOptions" :key="item.value" :label="item.label" :value="item.value" />
          </el-select>
        </el-form-item>
        <el-form-item label="场景">
          <el-select v-model="filters.scene_id" clearable placeholder="全部场景" style="width: 170px">
            <el-option v-for="scene in scenes" :key="scene.id" :label="scene.display_name" :value="scene.id" />
          </el-select>
        </el-form-item>
        <el-form-item label="日期">
          <el-date-picker
            v-model="dateRange"
            type="daterange"
            value-format="YYYY-MM-DD"
            start-placeholder="开始日期"
            end-placeholder="结束日期"
            range-separator="至"
            style="width: 260px"
          />
        </el-form-item>
        <el-form-item>
          <el-input v-model="filters.keyword" clearable placeholder="任务 ID / 场景 / 发起人" style="width: 210px" @keyup.enter="search" />
        </el-form-item>
        <el-form-item>
          <el-button type="primary" :icon="Search" @click="search">搜索</el-button>
          <el-button :icon="Refresh" @click="resetFilters">重置</el-button>
        </el-form-item>
      </el-form>
    </el-card>

    <el-card shadow="never">
      <el-table v-loading="loading" :data="tasks" stripe empty-text="暂无检测记录">
        <el-table-column prop="id" label="ID" width="80" />
        <el-table-column label="类型" width="110">
          <template #default="{ row }">{{ typeName(row.task_type) }}</template>
        </el-table-column>
        <el-table-column label="状态" width="110">
          <template #default="{ row }">
            <el-tag :type="statusTag(row.status)" effect="light">{{ statusName(row.status) }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="scene_name" label="场景" min-width="150" show-overflow-tooltip />
        <el-table-column label="发起人" min-width="130" show-overflow-tooltip>
          <template #default="{ row }">{{ initiatorName(row) }}</template>
        </el-table-column>
        <el-table-column prop="total_images" label="图像数" width="90" align="right" />
        <el-table-column prop="total_objects" label="目标数" width="90" align="right" />
        <el-table-column label="总耗时" width="120" align="right">
          <template #default="{ row }">{{ formatDuration(row.total_inference_time) }}</template>
        </el-table-column>
        <el-table-column label="创建时间" min-width="170">
          <template #default="{ row }">{{ formatDate(row.created_at) }}</template>
        </el-table-column>
        <el-table-column label="操作" width="150" fixed="right">
          <template #default="{ row }">
            <el-button link type="primary" :icon="View" @click="openDetail(row.id)">详情</el-button>
            <el-button v-if="canDeleteHistory" link type="danger" :icon="Delete" @click="removeTask(row)">删除</el-button>
          </template>
        </el-table-column>
      </el-table>

      <div class="pagination-wrap">
        <el-pagination
          v-model:current-page="pagination.page"
          v-model:page-size="pagination.page_size"
          :page-sizes="[10, 20, 50, 100]"
          :total="pagination.total"
          layout="total, sizes, prev, pager, next, jumper"
          @current-change="loadTasks"
          @size-change="handleSizeChange"
        />
      </div>
    </el-card>

    <el-drawer v-model="detailVisible" title="检测任务详情" size="min(760px, 90%)" destroy-on-close>
      <div v-loading="detailLoading">
        <template v-if="detail">
          <el-descriptions :column="2" border>
            <el-descriptions-item label="任务 ID">#{{ detail.task.id }}</el-descriptions-item>
            <el-descriptions-item label="类型">{{ typeName(detail.task.task_type) }}</el-descriptions-item>
            <el-descriptions-item label="状态">
              <el-tag :type="statusTag(detail.task.status)">{{ statusName(detail.task.status) }}</el-tag>
            </el-descriptions-item>
            <el-descriptions-item label="场景">{{ detail.task.scene_name || '-' }}</el-descriptions-item>
            <el-descriptions-item label="检测发起人">{{ initiatorName(detail.task) }}</el-descriptions-item>
            <el-descriptions-item label="图像 / 目标">{{ detail.task.total_images }} / {{ detail.task.total_objects }}</el-descriptions-item>
            <el-descriptions-item label="总耗时">{{ formatDuration(detail.task.total_inference_time) }}</el-descriptions-item>
            <el-descriptions-item label="置信度阈值 / NMS IoU阈值">{{ formatThreshold(detail.task.conf_threshold) }} / {{ formatThreshold(detail.task.iou_threshold) }}</el-descriptions-item>
            <el-descriptions-item label="创建时间">{{ formatDate(detail.task.created_at) }}</el-descriptions-item>
            <el-descriptions-item label="完成时间" :span="2">{{ formatDate(detail.task.completed_at) }}</el-descriptions-item>
            <el-descriptions-item v-if="detail.task.error_message" label="错误信息" :span="2">{{ detail.task.error_message }}</el-descriptions-item>
          </el-descriptions>

          <section class="detail-section">
            <h3>类别统计</h3>
            <el-space wrap>
              <el-tag v-for="(count, name) in detail.class_counts" :key="name" type="info">{{ name }} × {{ count }}</el-tag>
              <span v-if="Object.keys(detail.class_counts).length === 0" class="empty-text">暂无目标</span>
            </el-space>
          </section>

          <section class="detail-section">
            <h3>检测结果</h3>
            <el-table :data="detail.results" max-height="420" size="small" empty-text="暂无检测目标">
              <el-table-column prop="image_path" label="图像/帧" min-width="150" show-overflow-tooltip />
              <el-table-column label="类别" min-width="110">
                <template #default="{ row }">{{ row.class_name_cn || row.class_name }}</template>
              </el-table-column>
              <el-table-column label="置信度" width="90">
                <template #default="{ row }">{{ formatConfidence(row.confidence) }}</template>
              </el-table-column>
              <el-table-column label="边界框" min-width="170">
                <template #default="{ row }">{{ formatBbox(row.bbox) }}</template>
              </el-table-column>
              <el-table-column label="耗时" width="100">
                <template #default="{ row }">{{ formatDuration(row.inference_time) }}</template>
              </el-table-column>
            </el-table>
          </section>
        </template>
      </div>
    </el-drawer>
  </div>
</template>

<script setup>
import {
  deleteTask,
  getHistorySummary,
  getScenes,
  getTaskDetail,
  getTaskList,
} from '@/api/history'
import { Delete, Refresh, Search, View } from '@element-plus/icons-vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { computed, onMounted, reactive, ref } from 'vue'
import { useUserStore } from '@/stores/user'

const userStore = useUserStore()
const canDeleteHistory = computed(() => userStore.hasPermission('history:delete:any'))

const typeOptions = [
  { label: '单图检测', value: 'single' },
  { label: '批量检测', value: 'batch' },
  { label: '视频检测', value: 'video' },
]
const statusOptions = [
  { label: '待处理', value: 'pending' },
  { label: '处理中', value: 'processing' },
  { label: '已完成', value: 'completed' },
  { label: '失败', value: 'failed' },
]
const typeNameMap = {
  single: '单图检测',
  batch: '批量检测',
  zip: '批量检测',
  folder: '批量检测',
  video: '视频检测',
  camera: '摄像头检测',
}
const filters = reactive({ task_type: '', status: '', scene_id: null, keyword: '' })
const dateRange = ref(null)
const scenes = ref([])
const tasks = ref([])
const loading = ref(false)
const pagination = reactive({ page: 1, page_size: 10, total: 0 })
const summary = reactive({
  total_tasks: 0,
  today_tasks: 0,
  status_counts: { completed: 0, processing: 0, failed: 0, pending: 0 },
})
const detailVisible = ref(false)
const detailLoading = ref(false)
const detail = ref(null)

function typeName(value) {
  return typeNameMap[value] || value || '-'
}

function statusName(value) {
  return statusOptions.find((item) => item.value === value)?.label || value || '-'
}

function statusTag(value) {
  return { completed: 'success', processing: 'warning', failed: 'danger', pending: 'info' }[value] || 'info'
}

function formatDate(value) {
  return value ? new Date(value).toLocaleString('zh-CN', { hour12: false }) : '-'
}

function initiatorName(task) {
  if (task?.initiator_username) return task.initiator_username
  return task?.initiator_user_id ? `用户 #${task.initiator_user_id}` : '-'
}

function formatDuration(value) {
  return value === null || value === undefined ? '-' : `${Number(value).toFixed(2)} ms`
}

function formatConfidence(value) {
  return `${(Number(value || 0) * 100).toFixed(1)}%`
}

function formatThreshold(value) {
  return value === null || value === undefined ? '-' : Number(value).toFixed(2)
}

function formatBbox(value) {
  return Array.isArray(value) ? `[${value.map((item) => Number(item).toFixed(1)).join(', ')}]` : '-'
}

function buildParams() {
  const params = {
    page: pagination.page,
    page_size: pagination.page_size,
    task_type: filters.task_type || undefined,
    status: filters.status || undefined,
    scene_id: filters.scene_id || undefined,
    keyword: filters.keyword.trim() || undefined,
  }
  if (dateRange.value?.length === 2) {
    params.start_date = dateRange.value[0]
    params.end_date = dateRange.value[1]
  }
  return params
}

async function loadTasks() {
  loading.value = true
  try {
    const result = await getTaskList(buildParams())
    tasks.value = result.items || []
    pagination.total = result.total || 0
  } catch (error) {
    console.error('[历史记录加载失败]', error)
  } finally {
    loading.value = false
  }
}

async function loadSummary() {
  try {
    Object.assign(summary, await getHistorySummary())
  } catch (error) {
    console.error('[历史摘要加载失败]', error)
  }
}

function search() {
  pagination.page = 1
  loadTasks()
}

function resetFilters() {
  filters.task_type = ''
  filters.status = ''
  filters.scene_id = null
  filters.keyword = ''
  dateRange.value = null
  search()
}

function handleSizeChange() {
  pagination.page = 1
  loadTasks()
}

async function openDetail(taskId) {
  detailVisible.value = true
  detailLoading.value = true
  detail.value = null
  try {
    detail.value = await getTaskDetail(taskId)
  } catch (error) {
    detailVisible.value = false
    console.error('[任务详情加载失败]', error)
  } finally {
    detailLoading.value = false
  }
}

async function removeTask(row) {
  try {
    await ElMessageBox.confirm(
      `确定删除任务 #${row.id}？关联的检测结果也会被删除。`,
      '删除确认',
      { type: 'warning', confirmButtonText: '删除', cancelButtonText: '取消' },
    )
    await deleteTask(row.id)
    ElMessage.success('任务已删除')
    if (tasks.value.length === 1 && pagination.page > 1) pagination.page -= 1
    await Promise.all([loadTasks(), loadSummary()])
  } catch (error) {
    if (error !== 'cancel' && error !== 'close') console.error('[删除任务失败]', error)
  }
}

onMounted(async () => {
  try {
    const sceneResult = await getScenes()
    scenes.value = sceneResult.scenes || []
  } catch (error) {
    console.error('[场景列表加载失败]', error)
  }
  await Promise.all([loadTasks(), loadSummary()])
})
</script>

<style lang="scss" scoped>
.history-page { min-height: 100%; }
.page-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  margin-bottom: 20px;
  h2 { margin: 0 0 4px; }
  p { margin: 0; color: $text-secondary; font-size: 14px; }
}
.summary-list {
  display: flex;
  gap: 20px;
  color: $text-secondary;
  font-size: 14px;
  strong { margin-left: 4px; color: $text-primary; font-size: 18px; }
}
.filter-card {
  margin-bottom: 16px;
  :deep(.el-card__body) { padding-bottom: 2px; }
}
.pagination-wrap { display: flex; justify-content: flex-end; margin-top: 20px; }
.detail-section {
  margin-top: 24px;
  h3 { margin: 0 0 12px; font-size: 16px; }
}
.empty-text { color: $text-secondary; font-size: 14px; }
@media (max-width: 768px) {
  .page-header { align-items: flex-start; flex-direction: column; }
  .summary-list { flex-wrap: wrap; gap: 10px 16px; }
}
</style>
