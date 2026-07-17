<template>
  <div class="model-page">
    <div class="page-header">
      <div>
        <h2>模型管理</h2>
        <p>管理模型版本、全局切换、归档删除及备份恢复</p>
      </div>
      <el-button :icon="Refresh" :loading="loading" @click="loadAll">刷新</el-button>
    </div>

    <el-card class="current-card" shadow="never">
      <template #header>
        <div class="card-title">
          <span>当前全局检测模型</span>
          <el-tag v-if="currentModel" type="success" effect="dark">运行中</el-tag>
          <el-tag v-else type="warning">未配置</el-tag>
        </div>
      </template>
      <el-descriptions v-if="currentModel" :column="4" border>
        <el-descriptions-item label="版本">{{ currentModel.version }}</el-descriptions-item>
        <el-descriptions-item label="模型名称">{{ currentModel.model_name }}</el-descriptions-item>
        <el-descriptions-item label="检测场景">{{ currentModel.scene_name || '-' }}</el-descriptions-item>
        <el-descriptions-item label="模型类型">{{ currentModel.model_type }}</el-descriptions-item>
        <el-descriptions-item label="mAP@50">{{ formatPercent(currentModel.map50) }}</el-descriptions-item>
        <el-descriptions-item label="mAP@50-95">{{ formatPercent(currentModel.map50_95) }}</el-descriptions-item>
        <el-descriptions-item label="已执行任务">{{ currentModel.detection_task_count }}</el-descriptions-item>
        <el-descriptions-item label="权重状态">
          <el-tag :type="currentModel.file_exists ? 'success' : 'danger'">
            {{ currentModel.file_exists ? '文件可用' : '文件缺失' }}
          </el-tag>
        </el-descriptions-item>
        <el-descriptions-item label="模型备份">
          <el-tag :type="currentModel.backup_available ? 'success' : 'info'">
            {{ currentModel.backup_available ? '已备份' : '未备份' }}
          </el-tag>
        </el-descriptions-item>
        <el-descriptions-item label="最近备份">{{ formatDate(currentModel.backed_up_at) }}</el-descriptions-item>
      </el-descriptions>
      <el-empty v-else description="尚未配置可用的全局模型，请从下方版本列表中选择" :image-size="72" />
    </el-card>

    <el-card shadow="never">
      <div class="toolbar">
        <el-select v-model="filters.scene_id" clearable placeholder="全部场景" style="width: 190px" @change="loadVersions">
          <el-option v-for="scene in scenes" :key="scene.id" :label="scene.display_name" :value="scene.id" />
        </el-select>
        <el-select v-model="filters.status" clearable placeholder="全部状态" style="width: 140px" @change="loadVersions">
          <el-option label="启用" value="active" />
          <el-option label="已归档" value="archived" />
          <el-option label="已删除" value="deleted" />
        </el-select>
      </div>

      <el-table v-loading="loading" :data="versions" stripe empty-text="暂无模型版本">
        <el-table-column label="当前" width="78" align="center">
          <template #default="{ row }">
            <el-tag v-if="row.is_global_default" type="success" effect="dark" size="small">当前</el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="version" label="版本" width="110" />
        <el-table-column prop="model_name" label="模型名称" min-width="210" show-overflow-tooltip />
        <el-table-column prop="model_type" label="类型" width="110" />
        <el-table-column prop="scene_name" label="场景" min-width="150" show-overflow-tooltip />
        <el-table-column label="状态" width="95" align="center">
          <template #default="{ row }">
            <el-tag :type="statusTag(row.status)" size="small">{{ statusName(row.status) }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="mAP@50" width="105" align="right">
          <template #default="{ row }">{{ formatPercent(row.map50) }}</template>
        </el-table-column>
        <el-table-column label="mAP@50-95" width="120" align="right">
          <template #default="{ row }">{{ formatPercent(row.map50_95) }}</template>
        </el-table-column>
        <el-table-column label="权重" width="100" align="center">
          <template #default="{ row }">
            <el-tag :type="row.file_exists ? 'success' : 'danger'" size="small">
              {{ row.file_exists ? '可用' : '缺失' }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="备份" width="105" align="center">
          <template #default="{ row }">
            <el-tag :type="row.backup_available ? 'success' : 'info'" size="small">
              {{ row.backup_available ? '已备份' : '未备份' }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="detection_task_count" label="任务数" width="90" align="right" />
        <el-table-column label="创建时间" min-width="170">
          <template #default="{ row }">{{ formatDate(row.created_at) }}</template>
        </el-table-column>
        <el-table-column label="操作" width="300" fixed="right">
          <template #default="{ row }">
            <el-button link type="primary" :disabled="!row.file_exists || row.status !== 'active'" @click="openTest(row)">测试</el-button>
            <el-button
              link
              type="primary"
              :disabled="!row.training_task_id || row.status !== 'active'"
              :loading="evaluatingId === row.id"
              @click="startEvaluation(row)"
            >评估</el-button>
            <el-button
              link
              type="success"
              :disabled="row.is_global_default || row.status !== 'active' || !row.file_exists"
              :loading="activatingId === row.id"
              @click="switchModel(row)"
            >设为全局</el-button>
            <el-dropdown
              trigger="click"
              :disabled="operatingKey?.endsWith(`:${row.id}`)"
              @command="handleMoreCommand($event, row)"
            >
              <el-button link type="primary" :loading="operatingKey?.endsWith(`:${row.id}`)">更多</el-button>
              <template #dropdown>
                <el-dropdown-menu>
                  <el-dropdown-item command="backup" :disabled="row.status === 'deleted' || !row.file_exists">备份模型</el-dropdown-item>
                  <el-dropdown-item command="restore" :disabled="row.status === 'deleted' || !row.backup_available || row.file_exists">从备份恢复</el-dropdown-item>
                  <el-dropdown-item v-if="row.status === 'active'" command="archive" :disabled="row.is_global_default" divided>归档</el-dropdown-item>
                  <el-dropdown-item v-if="row.status === 'archived'" command="unarchive" divided>取消归档</el-dropdown-item>
                  <el-dropdown-item v-if="row.status !== 'deleted'" command="delete" :disabled="row.is_global_default" divided>删除</el-dropdown-item>
                </el-dropdown-menu>
              </template>
            </el-dropdown>
          </template>
        </el-table-column>
      </el-table>
    </el-card>

    <el-dialog v-model="testVisible" :title="`模型测试 · ${testTarget?.version || ''}`" width="880px" destroy-on-close>
      <el-upload
        drag
        :auto-upload="false"
        :limit="1"
        accept="image/jpeg,image/png,image/bmp,image/webp"
        :on-change="handleTestFile"
        :on-remove="clearTestFile"
      >
        <el-icon class="el-icon--upload"><UploadFilled /></el-icon>
        <div class="el-upload__text">拖拽测试图片到此处，或 <em>点击选择</em></div>
      </el-upload>
      <el-row :gutter="24" class="thresholds">
        <el-col :span="12">
          <span>置信度：{{ testForm.conf.toFixed(2) }}</span>
          <el-slider v-model="testForm.conf" :min="0.05" :max="0.95" :step="0.05" />
        </el-col>
        <el-col :span="12">
          <span>IoU：{{ testForm.iou.toFixed(2) }}</span>
          <el-slider v-model="testForm.iou" :min="0.1" :max="0.9" :step="0.05" />
        </el-col>
      </el-row>
      <div v-if="testResult" class="test-result">
        <el-descriptions :column="3" border>
          <el-descriptions-item label="模型版本">{{ testResult.model_version }}</el-descriptions-item>
          <el-descriptions-item label="检测目标">{{ testResult.total_objects }}</el-descriptions-item>
          <el-descriptions-item label="推理耗时">{{ testResult.inference_time }} ms</el-descriptions-item>
        </el-descriptions>
        <img :src="`data:image/jpeg;base64,${testResult.annotated_image}`" class="result-image" alt="模型测试标注结果" />
      </div>
      <template #footer>
        <el-button @click="testVisible = false">关闭</el-button>
        <el-button type="primary" :disabled="!testForm.file" :loading="testing" @click="runTest">开始测试</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="evaluationVisible" title="模型评估结果" width="720px">
      <div v-if="evaluationReport">
        <el-descriptions :column="2" border>
          <el-descriptions-item label="数据集划分">{{ evaluationReport.split }}</el-descriptions-item>
          <el-descriptions-item label="模型版本">{{ evaluationTarget?.version }}</el-descriptions-item>
          <el-descriptions-item label="Precision">{{ formatPercent(evaluationReport.overall?.precision) }}</el-descriptions-item>
          <el-descriptions-item label="Recall">{{ formatPercent(evaluationReport.overall?.recall) }}</el-descriptions-item>
          <el-descriptions-item label="mAP@50">{{ formatPercent(evaluationReport.overall?.map50) }}</el-descriptions-item>
          <el-descriptions-item label="mAP@50-95">{{ formatPercent(evaluationReport.overall?.map50_95) }}</el-descriptions-item>
        </el-descriptions>
        <el-table :data="perClassRows" class="per-class-table" empty-text="暂无分类指标">
          <el-table-column prop="name" label="类别" min-width="180" />
          <el-table-column label="AP@50"><template #default="{ row }">{{ formatPercent(row.ap50) }}</template></el-table-column>
          <el-table-column label="AP@50-95"><template #default="{ row }">{{ formatPercent(row.ap50_95) }}</template></el-table-column>
          <el-table-column prop="instances" label="样本数" />
        </el-table>
      </div>
    </el-dialog>
  </div>
</template>

<script setup>
import { computed, onBeforeUnmount, onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Refresh, UploadFilled } from '@element-plus/icons-vue'
import request, { getApiErrorMessage } from '@/utils/request'
import {
  activateModel,
  archiveModel,
  backupModel,
  deleteModel,
  evaluateModel,
  getCurrentModel,
  getModelEvaluation,
  getModelVersions,
  restoreModel,
  testModel,
  unarchiveModel,
} from '@/api/models'

const loading = ref(false)
const versions = ref([])
const scenes = ref([])
const currentModel = ref(null)
const activatingId = ref(null)
const operatingKey = ref('')
const evaluatingId = ref(null)
const evaluationTarget = ref(null)
const evaluationReport = ref(null)
const evaluationVisible = ref(false)
const filters = reactive({ scene_id: null, status: '' })
let evaluationTimer = null

const testVisible = ref(false)
const testTarget = ref(null)
const testing = ref(false)
const testResult = ref(null)
const testForm = reactive({ file: null, conf: 0.25, iou: 0.45 })

const perClassRows = computed(() => Object.entries(evaluationReport.value?.per_class || {}).map(([name, metrics]) => ({
  name,
  ...metrics,
})))

function formatPercent(value) {
  return value === null || value === undefined ? '-' : `${(Number(value) * 100).toFixed(1)}%`
}

function formatDate(value) {
  return value ? new Date(value).toLocaleString('zh-CN', { hour12: false }) : '-'
}

const statusNames = { active: '启用', archived: '已归档', deleted: '已删除' }
const statusTags = { active: 'success', archived: 'warning', deleted: 'danger' }
function statusName(status) { return statusNames[status] || status }
function statusTag(status) { return statusTags[status] || 'info' }

async function loadCurrent() {
  try {
    currentModel.value = await getCurrentModel()
  } catch (error) {
    currentModel.value = null
    if (![404, 409].includes(error.response?.status)) throw error
  }
}

async function loadVersions() {
  const result = await getModelVersions({
    scene_id: filters.scene_id || undefined,
    status: filters.status || undefined,
  })
  versions.value = result.items || []
}

async function loadAll() {
  loading.value = true
  try {
    const [, sceneResult] = await Promise.all([
      Promise.all([loadCurrent(), loadVersions()]),
      request.get('/training/scenes'),
    ])
    scenes.value = sceneResult.items || []
  } finally {
    loading.value = false
  }
}

async function switchModel(row) {
  const confirmed = await confirmAction(
    `切换后，所有新检测任务将使用 ${row.version}。是否继续？`,
    '切换全局模型',
    { type: 'warning', confirmButtonText: '确认切换', cancelButtonText: '取消' },
  )
  if (!confirmed) return
  activatingId.value = row.id
  try {
    const result = await activateModel(row.id)
    ElMessage.success(result.message)
    await Promise.all([loadCurrent(), loadVersions()])
  } finally {
    activatingId.value = null
  }
}

async function confirmAction(message, title, options) {
  try {
    await ElMessageBox.confirm(message, title, options)
    return true
  } catch (action) {
    if (action === 'cancel' || action === 'close') return false
    throw action
  }
}

async function runRowOperation(command, row, operation) {
  operatingKey.value = `${command}:${row.id}`
  try {
    const result = await operation()
    ElMessage.success(result.message)
    await Promise.all([loadCurrent(), loadVersions()])
  } finally {
    operatingKey.value = ''
  }
}

async function handleMoreCommand(command, row) {
  if (command === 'backup') {
    await runRowOperation(command, row, () => backupModel(row.id))
    return
  }
  if (command === 'restore') {
    const confirmed = await confirmAction(
      `将从备份恢复模型版本 ${row.version}，并校验文件完整性。是否继续？`,
      '恢复模型',
      { type: 'warning', confirmButtonText: '确认恢复', cancelButtonText: '取消' },
    )
    if (!confirmed) return
    await runRowOperation(command, row, () => restoreModel(row.id))
    return
  }
  if (command === 'archive') {
    const confirmed = await confirmAction(
      `归档 ${row.version} 后不能用于新检测，模型文件和备份都会保留。`,
      '确认归档',
      { type: 'warning', confirmButtonText: '确认归档', cancelButtonText: '取消' },
    )
    if (!confirmed) return
    await runRowOperation(command, row, () => archiveModel(row.id))
    return
  }
  if (command === 'unarchive') {
    await runRowOperation(command, row, () => unarchiveModel(row.id))
    return
  }
  if (command === 'delete') {
    const confirmed = await confirmAction(
      `确定删除模型版本 ${row.version}？删除后该版本将无法继续使用，相关备份也会一并清理，历史检测记录不受影响。`,
      '二次确认：删除模型',
      {
        type: 'error',
        confirmButtonText: '确认删除',
        cancelButtonText: '取消',
        distinguishCancelAndClose: true,
      },
    )
    if (!confirmed) return
    await runRowOperation(command, row, () => deleteModel(row.id))
  }
}

function openTest(row) {
  testTarget.value = row
  testForm.file = null
  testResult.value = null
  testVisible.value = true
}

function handleTestFile(uploadFile) {
  testForm.file = uploadFile.raw
  testResult.value = null
}

function clearTestFile() {
  testForm.file = null
  testResult.value = null
}

async function runTest() {
  if (!testForm.file || !testTarget.value) return
  const formData = new FormData()
  formData.append('file', testForm.file)
  formData.append('conf', String(testForm.conf))
  formData.append('iou', String(testForm.iou))
  testing.value = true
  try {
    testResult.value = await testModel(testTarget.value.id, formData)
    ElMessage.success('模型测试完成')
  } finally {
    testing.value = false
  }
}

function clearEvaluationTimer() {
  if (evaluationTimer) window.clearTimeout(evaluationTimer)
  evaluationTimer = null
}

async function pollEvaluation(modelVersionId) {
  clearEvaluationTimer()
  try {
    const status = await getModelEvaluation(modelVersionId)
    if (status.status === 'completed') {
      evaluatingId.value = null
      evaluationReport.value = status.report
      evaluationVisible.value = true
      ElMessage.success('模型评估完成')
      await Promise.all([loadCurrent(), loadVersions()])
      return
    }
    if (status.status === 'failed' || status.status === 'unavailable') {
      evaluatingId.value = null
      ElMessage.error(status.error || '模型评估失败')
      return
    }
    evaluationTimer = window.setTimeout(() => pollEvaluation(modelVersionId), 3000)
  } catch (error) {
    evaluatingId.value = null
    ElMessage.error(getApiErrorMessage(error.response, '查询评估状态失败'))
  }
}

async function startEvaluation(row) {
  evaluationTarget.value = row
  evaluationReport.value = null
  evaluatingId.value = row.id
  try {
    await evaluateModel(row.id, { split: 'val', conf: 0.001, iou: 0.6 })
    ElMessage.info('评估已在后台启动')
    await pollEvaluation(row.id)
  } catch (error) {
    evaluatingId.value = null
  }
}

onMounted(loadAll)
onBeforeUnmount(clearEvaluationTimer)
</script>

<style scoped>
.model-page { padding: 20px; }
.page-header, .card-title, .toolbar { display: flex; align-items: center; justify-content: space-between; }
.page-header { margin-bottom: 18px; }
.page-header h2 { margin: 0 0 6px; }
.page-header p { margin: 0; color: #909399; }
.current-card { margin-bottom: 18px; }
.toolbar { justify-content: flex-start; gap: 12px; margin-bottom: 16px; }
.thresholds { margin: 22px 0 8px; }
.test-result { margin-top: 18px; }
.result-image { display: block; max-width: 100%; max-height: 520px; margin: 18px auto 0; border-radius: 6px; }
.per-class-table { margin-top: 18px; }
</style>
