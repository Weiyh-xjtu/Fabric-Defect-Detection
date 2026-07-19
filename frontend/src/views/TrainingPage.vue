<template>
  <div class="training-page">
    <!-- ── 页面标题 ── -->
    <div class="page-header">
      <h2>模型训练与监控</h2>
      <el-button v-if="activeTab === 'training'" type="primary" @click="openCreateDialog">
        <el-icon><Plus /></el-icon>新建训练任务
      </el-button>
    </div>

    <el-tabs v-model="activeTab" class="training-tabs">
      <el-tab-pane label="训练任务" name="training">

    <!-- ── 训练任务列表 ── -->
    <el-card class="task-list-card" shadow="never">
      <template #header>
        <div class="card-header">
          <span>训练任务列表</span>
          <el-button text @click="fetchTasks">
            <el-icon><Refresh /></el-icon>刷新
          </el-button>
        </div>
      </template>

      <el-table :data="taskList" stripe style="width: 100%" v-loading="loadingTasks">
        <el-table-column prop="task_uuid" label="任务 ID" width="100" />
        <el-table-column prop="model_name" label="模型" width="110" />
        <el-table-column prop="device" label="设备" width="80" />
        <el-table-column label="进度" width="180">
          <template #default="{ row }">
            <el-progress
              :percentage="row.progress"
              :status="row.status === 'completed' ? 'success' : row.status === 'failed' ? 'exception' : ''"
              :stroke-width="16"
            />
          </template>
        </el-table-column>
        <el-table-column label="Epoch" width="100">
          <template #default="{ row }">
            {{ row.current_epoch }}/{{ row.epochs }}
          </template>
        </el-table-column>
        <el-table-column label="状态" width="100">
          <template #default="{ row }">
            <el-tag :type="statusType(row.status)" size="small">
              {{ statusText(row.status) }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="created_at" label="创建时间" width="170" />
        <el-table-column label="操作" width="200" fixed="right">
          <template #default="{ row }">
            <el-button
              size="small"
              type="primary"
              text
              @click="selectTask(row)"
            >
              监控
            </el-button>
            <el-button
              v-if="row.status === 'running'"
              size="small"
              type="danger"
              text
              @click="stopTask(row.id)"
            >
              停止
            </el-button>
          </template>
        </el-table-column>
      </el-table>
    </el-card>

    <!-- ── 训练监控面板 ── -->
    <el-card v-if="selectedTask" class="monitor-card" shadow="never">
      <template #header>
        <div class="card-header">
          <span>
            训练监控 — 任务 {{ selectedTask.task_uuid }}
            <el-tag :type="statusType(selectedTask.status)" size="small" style="margin-left: 8px">
              {{ statusText(selectedTask.status) }}
            </el-tag>
          </span>
          <div class="monitor-info">
            <span>模型: {{ selectedTask.model_name }}</span>
            <span>设备: {{ selectedTask.device }}</span>
            <span>Epoch: {{ selectedTask.current_epoch }}/{{ selectedTask.epochs }}</span>
          </div>
        </div>
      </template>

      <!-- 最新指标卡片 -->
      <el-row :gutter="16" class="metric-cards">
        <el-col :span="4" v-for="item in metricCards" :key="item.label">
          <el-card shadow="hover" class="metric-item">
            <div class="metric-value">{{ item.value }}</div>
            <div class="metric-label">{{ item.label }}</div>
          </el-card>
        </el-col>
      </el-row>

      <!-- 训练曲线图表 -->
      <el-row :gutter="16" style="margin-top: 16px">
        <el-col :span="12">
          <div ref="lossChartRef" style="height: 350px"></div>
        </el-col>
        <el-col :span="12">
          <div ref="mapChartRef" style="height: 350px"></div>
        </el-col>
      </el-row>
      <el-card v-if="isModelReadyTask(selectedTask)" class="model-actions" shadow="never">
        <template #header>
          <div class="card-header">
            <span>模型评估与导出</span>
            <span class="action-hint">基于任务 {{ selectedTask.task_uuid }} 的 best.pt</span>
          </div>
        </template>
        <el-space wrap>
          <el-button type="primary" :loading="validating" @click="validateModel">模型评估</el-button>
          <el-button type="success" @click="showExportDialog = true">导出模型</el-button>
          <el-button @click="downloadModel">下载权重</el-button>
          <el-button @click="openPredictDialog">测试验证</el-button>
        </el-space>

        <div v-if="evalReport" class="evaluation-report">
          <div class="evaluation-meta">
            <el-tag v-if="evalReport.cached" type="info">复用历史评估</el-tag>
            <span v-if="evalReport.evaluated_at">评估时间：{{ formatEvaluationTime(evalReport.evaluated_at) }}</span>
          </div>
          <el-row :gutter="16" class="evaluation-metrics">
            <el-col v-for="metric in evaluationCards" :key="metric.label" :xs="12" :sm="6">
              <div class="evaluation-metric">
                <div class="metric-value">{{ metric.value }}</div>
                <div class="metric-label">{{ metric.label }}</div>
              </div>
            </el-col>
          </el-row>
          <h4>分类 AP</h4>
          <el-table
            :data="perClassMetrics"
            :row-class-name="evaluationRowClass"
            empty-text="暂无分类指标"
          >
            <el-table-column prop="className" label="类别" min-width="160" />
            <el-table-column label="AP@50" min-width="130">
              <template #default="{ row }">
                <span>{{ formatPercent(row.ap50) }}</span>
                <el-tag v-if="row.ap50 < 0.5" type="danger" size="small" class="weak-tag">需调优</el-tag>
              </template>
            </el-table-column>
            <el-table-column label="AP@50-95" min-width="130">
              <template #default="{ row }">{{ formatPercent(row.ap50_95) }}</template>
            </el-table-column>
            <el-table-column prop="instances" label="样本数" min-width="100">
              <template #default="{ row }">{{ row.instances ?? '-' }}</template>
            </el-table-column>
          </el-table>
          <div v-if="evaluationArtifacts.length" class="evaluation-artifacts">
            <h4>评估图表</h4>
            <el-space wrap>
              <el-button
                v-for="artifact in evaluationArtifacts"
                :key="artifact.name"
                size="small"
                @click="openEvaluationArtifact(artifact)"
              >{{ artifact.name }}</el-button>
            </el-space>
          </div>
        </div>
      </el-card>
    </el-card>

      </el-tab-pane>
      <el-tab-pane label="数据集管理" name="datasets" lazy>
        <DatasetPanel @scenes-changed="fetchScenes" />
      </el-tab-pane>
    </el-tabs>

    <el-dialog
      v-model="showCreateDialog"
      title="新建训练任务"
      width="600px"
      :close-on-click-modal="false"
    >
      <el-form :model="trainForm" label-width="120px">
        <el-form-item label="检测场景">
          <el-select
            v-model="trainForm.scene_id"
            placeholder="选择场景"
            :loading="loadingScenes"
            :disabled="!sceneList.length"
          >
            <el-option
              v-for="scene in sceneList"
              :key="scene.id"
              :label="scene.display_name"
              :value="scene.id"
            />
          </el-select>
        </el-form-item>

        <el-form-item label="基础模型">
          <el-select v-model="trainForm.model_name">
            <el-option label="YOLOv11n (Nano, 最快)" value="yolo11n" />
            <el-option label="YOLOv11s (Small)" value="yolo11s" />
            <el-option label="YOLOv11m (Medium)" value="yolo11m" />
            <el-option label="YOLOv11l (Large)" value="yolo11l" />
            <el-option label="YOLOv11x (XLarge, 最强)" value="yolo11x" />
          </el-select>
        </el-form-item>

        <el-form-item label="训练轮数">
          <el-slider v-model="trainForm.epochs" :min="5" :max="500" :step="10" show-input />
        </el-form-item>

        <el-form-item label="批次大小">
          <el-input-number v-model="trainForm.batch_size" :min="1" :max="64" :step="2" />
        </el-form-item>

        <el-form-item label="图像尺寸">
          <el-select v-model="trainForm.img_size">
            <el-option label="416" :value="416" />
            <el-option label="512" :value="512" />
            <el-option label="640 (默认)" :value="640" />
            <el-option label="768" :value="768" />
          </el-select>
        </el-form-item>

        <el-form-item label="训练设备">
          <el-radio-group v-model="trainForm.device">
            <el-radio value="cpu">CPU (本地)</el-radio>
            <el-radio value="0">GPU:0</el-radio>
            <el-radio value="1">GPU:1</el-radio>
          </el-radio-group>
        </el-form-item>

        <el-form-item label="优化器">
          <el-select v-model="trainForm.optimizer">
            <el-option label="SGD (推荐)" value="SGD" />
            <el-option label="Adam" value="Adam" />
            <el-option label="AdamW" value="AdamW" />
          </el-select>
        </el-form-item>

        <el-form-item label="初始学习率">
          <el-input-number
            v-model="trainForm.lr0"
            :min="0.0001"
            :max="0.1"
            :step="0.001"
            :precision="4"
          />
        </el-form-item>

        <el-divider content-position="left">数据增强调优</el-divider>
        <el-form-item label="Mosaic">
          <el-slider v-model="trainForm.augment_config.mosaic" :min="0" :max="1" :step="0.1" show-input />
        </el-form-item>
        <el-form-item label="MixUp">
          <el-slider v-model="trainForm.augment_config.mixup" :min="0" :max="1" :step="0.05" show-input />
        </el-form-item>
        <el-form-item label="旋转角度">
          <el-slider v-model="trainForm.augment_config.degrees" :min="0" :max="45" :step="1" show-input />
        </el-form-item>
        <el-form-item label="平移比例">
          <el-slider v-model="trainForm.augment_config.translate" :min="0" :max="0.5" :step="0.05" show-input />
        </el-form-item>
        <el-form-item label="缩放比例">
          <el-slider v-model="trainForm.augment_config.scale" :min="0" :max="1" :step="0.05" show-input />
        </el-form-item>
        <el-form-item label="水平翻转">
          <el-slider v-model="trainForm.augment_config.fliplr" :min="0" :max="1" :step="0.1" show-input />
        </el-form-item>
      </el-form>

      <template #footer>
        <el-button @click="showCreateDialog = false">取消</el-button>
        <el-button
          type="primary"
          @click="createTask"
          :loading="creating"
          :disabled="!trainForm.scene_id"
        >
          启动训练
        </el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="showExportDialog" title="导出正式模型" width="520px" :close-on-click-modal="false">
      <el-form :model="exportForm" label-width="110px">
        <el-form-item label="版本号">
          <el-input v-model="exportForm.version" placeholder="留空自动生成，如 v1.0.0" />
        </el-form-item>
        <el-form-item label="版本描述">
          <el-input v-model="exportForm.description" type="textarea" :rows="3" maxlength="1000" show-word-limit />
        </el-form-item>
        <el-form-item label="设为全局模型">
          <el-switch v-model="exportForm.set_default" />
        </el-form-item>
        <el-form-item label="同时备份">
          <el-switch v-model="exportForm.upload_minio" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="showExportDialog = false">取消</el-button>
        <el-button type="primary" :loading="exporting" @click="exportModel">确认导出</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="showPredictDialog" title="测试图片验证" width="900px" :close-on-click-modal="false">
      <el-upload
        drag
        :auto-upload="false"
        :limit="1"
        accept="image/jpeg,image/png,image/bmp,image/webp"
        :on-change="handleTestFile"
        :on-remove="clearTestFile"
      >
        <el-icon class="el-icon--upload"><UploadFilled /></el-icon>
        <div class="el-upload__text">拖拽图片到此处，或 <em>点击选择</em></div>
        <template #tip>
          <div class="el-upload__tip">支持 JPEG、PNG、BMP、WebP；建议使用未参与训练或验证的新图片。</div>
        </template>
      </el-upload>

      <el-row :gutter="24" class="predict-thresholds">
        <el-col :span="12">
          <div class="slider-label">置信度阈值：{{ predictForm.conf.toFixed(2) }}</div>
          <el-slider v-model="predictForm.conf" :min="0.05" :max="0.95" :step="0.05" />
        </el-col>
        <el-col :span="12">
          <div class="slider-label">IoU 阈值：{{ predictForm.iou.toFixed(2) }}</div>
          <el-slider v-model="predictForm.iou" :min="0.1" :max="0.9" :step="0.05" />
        </el-col>
      </el-row>

      <div v-if="predictResult" class="predict-result">
        <el-descriptions :column="3" border>
          <el-descriptions-item label="检测目标">{{ predictResult.total_objects }}</el-descriptions-item>
          <el-descriptions-item label="推理耗时">{{ predictResult.inference_time }} ms</el-descriptions-item>
          <el-descriptions-item label="文件名">{{ predictResult.filename }}</el-descriptions-item>
        </el-descriptions>
        <img
          :src="`data:image/jpeg;base64,${predictResult.annotated_image}`"
          alt="模型预测标注结果"
          class="annotated-image"
        />
        <el-table :data="predictResult.detections" empty-text="未检测到目标">
          <el-table-column prop="class_name" label="类别" min-width="140" />
          <el-table-column label="置信度" min-width="120">
            <template #default="{ row }">{{ formatPercent(row.confidence) }}</template>
          </el-table-column>
          <el-table-column label="边界框" min-width="260">
            <template #default="{ row }">[{{ row.bbox.join(', ') }}]</template>
          </el-table-column>
        </el-table>
      </div>

      <template #footer>
        <el-button @click="showPredictDialog = false">关闭</el-button>
        <el-button type="primary" :disabled="!predictForm.file" :loading="predicting" @click="runPredict">
          开始预测
        </el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, onBeforeUnmount, nextTick } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Plus, Refresh, UploadFilled } from '@element-plus/icons-vue'
import * as echarts from 'echarts'
import request from '@/utils/request'
import DatasetPanel from '@/components/training/DatasetPanel.vue'

const MODEL_READY_TASK_STATUSES = new Set(['completed', 'cancelled'])

// ── 状态变量 ──
const taskList = ref([])
const loadingTasks = ref(false)
const sceneList = ref([])
const loadingScenes = ref(false)
const selectedTask = ref(null)
const showCreateDialog = ref(false)
const activeTab = ref('training')
const creating = ref(false)
const evalReport = ref(null)
const validating = ref(false)
const showExportDialog = ref(false)
const exporting = ref(false)
const exportForm = ref({
  version: '',
  description: '',
  set_default: false,
  upload_minio: true,
})
const showPredictDialog = ref(false)
const predicting = ref(false)
const predictResult = ref(null)
const predictForm = ref({
  file: null,
  conf: 0.25,
  iou: 0.45,
})

// ── 图表引用 ──
const lossChartRef = ref(null)
const mapChartRef = ref(null)
let lossChart = null
let mapChart = null

// ── 轮询定时器 ──
let pollTimer = null
let evalPollTimer = null
let evalPollFailures = 0
let metricsPolling = false

// ── 训练表单 ──
const trainForm = ref({
  scene_id: null,
  model_name: 'yolo11n',
  epochs: 50,
  batch_size: 8,
  img_size: 640,
  device: 'cpu',
  optimizer: 'SGD',
  lr0: 0.01,
  augment_config: {
    mosaic: 1,
    mixup: 0,
    degrees: 0,
    translate: 0.1,
    scale: 0.5,
    fliplr: 0.5,
  },
})

// ── 计算属性：最新指标卡片 ──
const metricCards = computed(() => {
  if (!selectedTask.value) return []
  const m = selectedTask.value.latest_metric
  if (!m) return [
    { label: 'Epoch', value: `${selectedTask.value.current_epoch}/${selectedTask.value.epochs}` },
    { label: '进度', value: `${selectedTask.value.progress}%` },
    { label: 'Box Loss', value: '-' },
    { label: 'Cls Loss', value: '-' },
    { label: 'mAP@50', value: '-' },
    { label: 'mAP@50-95', value: '-' },
  ]
  return [
    { label: 'Epoch', value: `${m.epoch}/${selectedTask.value.epochs}` },
    { label: 'Box Loss', value: m.box_loss != null ? m.box_loss.toFixed(4) : '-' },
    { label: 'Cls Loss', value: m.cls_loss != null ? m.cls_loss.toFixed(4) : '-' },
    { label: 'Precision', value: m.precision != null ? (m.precision * 100).toFixed(1) + '%' : '-' },
    { label: 'mAP@50', value: m.map50 != null ? (m.map50 * 100).toFixed(1) + '%' : '-' },
    { label: 'mAP@50-95', value: m.map50_95 != null ? (m.map50_95 * 100).toFixed(1) + '%' : '-' },
  ]
})

const evaluationCards = computed(() => {
  const overall = evalReport.value?.overall
  if (!overall) return []
  return [
    { label: 'Precision', value: formatPercent(overall.precision) },
    { label: 'Recall', value: formatPercent(overall.recall) },
    { label: 'mAP@50', value: formatPercent(overall.map50) },
    { label: 'mAP@50-95', value: formatPercent(overall.map50_95) },
  ]
})

const perClassMetrics = computed(() => {
  const entries = Object.entries(evalReport.value?.per_class || {})
  return entries
    .map(([className, metrics]) => ({ className, ...metrics }))
    .sort((a, b) => b.ap50 - a.ap50)
})
const evaluationArtifacts = computed(() => Object.entries(evalReport.value?.artifacts || {}).map(
  ([name, url]) => ({ name, url }),
))

function formatEvaluationTime(value) {
  return value ? new Date(value).toLocaleString('zh-CN', { hour12: false }) : '-'
}

async function openEvaluationArtifact(artifact) {
  try {
    const requestPath = artifact.url.startsWith('/api')
      ? artifact.url.slice(4)
      : artifact.url
    const blob = await request.get(requestPath, { responseType: 'blob' })
    const objectUrl = URL.createObjectURL(blob)
    window.open(objectUrl, '_blank', 'noopener,noreferrer')
    window.setTimeout(() => URL.revokeObjectURL(objectUrl), 60000)
  } catch (error) {
    console.error('打开评估图表失败', error)
    ElMessage.error('评估图表加载失败')
  }
}

function formatPercent(value) {
  return value == null ? '-' : `${(value * 100).toFixed(1)}%`
}

function evaluationRowClass({ row }) {
  return row.ap50 < 0.5 ? 'weak-class-row' : ''
}

// ── 状态映射 ──
function statusType(status) {
  const map = {
    pending: 'info',
    running: 'warning',
    completed: 'success',
    failed: 'danger',
    cancelled: 'info',
  }
  return map[status] || 'info'
}

function statusText(status) {
  const map = {
    pending: '等待中',
    running: '训练中',
    completed: '已完成',
    failed: '失败',
    cancelled: '已取消',
  }
  return map[status] || status
}

function isModelReadyTask(task) {
  return MODEL_READY_TASK_STATUSES.has(task?.status)
}

async function openCreateDialog() {
  await fetchScenes()
  showCreateDialog.value = true
}

// ── 获取可用检测场景 ──
async function fetchScenes() {
  loadingScenes.value = true
  try {
    const res = await request.get('/training/scenes')
    sceneList.value = res.items || []
    if (!sceneList.value.some((scene) => scene.id === trainForm.value.scene_id)) {
      trainForm.value.scene_id = sceneList.value[0]?.id ?? null
    }
  } catch (e) {
    console.error('获取检测场景失败', e)
  } finally {
    loadingScenes.value = false
  }
}

// ── 获取任务列表 ──
async function fetchTasks() {
  loadingTasks.value = true
  try {
    const res = await request.get('/training/tasks')
    taskList.value = res.items || []
  } catch (e) {
    console.error('获取任务列表失败', e)
  } finally {
    loadingTasks.value = false
  }
}

// ── 选择任务并开始监控 ──
async function selectTask(task) {
  stopEvalPolling()
  validating.value = false
  evalPollFailures = 0
  selectedTask.value = task
  evalReport.value = null
  predictResult.value = null
  await nextTick()
  initCharts()
  fetchMetrics()
  startPolling()
  restoreEvalState(task)
}

// ── 初始化 ECharts 图表 ──
function initCharts() {
  if (lossChart) lossChart.dispose()
  if (mapChart) mapChart.dispose()

  if (lossChartRef.value) {
    lossChart = echarts.init(lossChartRef.value)
  }
  if (mapChartRef.value) {
    mapChart = echarts.init(mapChartRef.value)
  }
}

// ── 获取训练指标并更新图表 ──
async function fetchMetrics() {
  if (!selectedTask.value || metricsPolling) return
  metricsPolling = true
  try {
    const taskId = selectedTask.value.id || selectedTask.value.task?.id
    const res = await request.get(`/training/metrics/${taskId}`, {
      skipGlobalError: true,
    })
    const metrics = res.metrics || []

    // 更新任务状态
    const statusRes = await request.get(`/training/status/${taskId}`, {
      skipGlobalError: true,
    })
    if (statusRes) {
      selectedTask.value = {
        ...selectedTask.value,
        ...statusRes.task,
        latest_metric: statusRes.latest_metric,
        is_running: statusRes.is_running,
      }
    }

    if (metrics.length > 0) {
      updateCharts(metrics)
    }
  } catch (e) {
    console.error('获取训练指标失败', e)
  } finally {
    metricsPolling = false
  }
}

// ── 更新图表 ──
// FIRESIGHT 品牌图表配色：靛蓝 / 朱橙 / 织物绿 / 纱线金
const CHART_COLORS = ['#6f7f9b', '#df6b4e', '#86a48c', '#d9b565']

function updateCharts(metrics) {
  const epochs = metrics.map((m) => m.epoch)

  // Loss 曲线
  if (lossChart) {
    lossChart.setOption({
      color: CHART_COLORS,
      title: { text: '训练损失曲线', left: 'center', textStyle: { fontSize: 14 } },
      tooltip: { trigger: 'axis' },
      legend: { data: ['Box Loss', 'Cls Loss', 'DFL Loss'], bottom: 0 },
      grid: { left: '10%', right: '5%', top: '15%', bottom: '15%' },
      xAxis: { type: 'category', data: epochs, name: 'Epoch' },
      yAxis: { type: 'value', name: 'Loss' },
      series: [
        {
          name: 'Box Loss',
          type: 'line',
          data: metrics.map((m) => m.box_loss),
          smooth: true,
          lineStyle: { width: 2 },
        },
        {
          name: 'Cls Loss',
          type: 'line',
          data: metrics.map((m) => m.cls_loss),
          smooth: true,
          lineStyle: { width: 2 },
        },
        {
          name: 'DFL Loss',
          type: 'line',
          data: metrics.map((m) => m.dfl_loss),
          smooth: true,
          lineStyle: { width: 2 },
        },
      ],
    })
  }

  // mAP 曲线
  if (mapChart) {
    mapChart.setOption({
      title: { text: '评估指标曲线', left: 'center', textStyle: { fontSize: 14 } },
      tooltip: { trigger: 'axis' },
      legend: { data: ['mAP@50', 'mAP@50-95', 'Precision', 'Recall'], bottom: 0 },
      grid: { left: '10%', right: '5%', top: '15%', bottom: '15%' },
      xAxis: { type: 'category', data: epochs, name: 'Epoch' },
      yAxis: { type: 'value', name: '指标值', max: 1 },
      series: [
        {
          name: 'mAP@50',
          type: 'line',
          data: metrics.map((m) => m.map50),
          smooth: true,
          lineStyle: { width: 2, color: '#df6b4e' },
          itemStyle: { color: '#df6b4e' },
        },
        {
          name: 'mAP@50-95',
          type: 'line',
          data: metrics.map((m) => m.map50_95),
          smooth: true,
          lineStyle: { width: 2, color: '#6f7f9b' },
          itemStyle: { color: '#6f7f9b' },
        },
        {
          name: 'Precision',
          type: 'line',
          data: metrics.map((m) => m.precision),
          smooth: true,
          lineStyle: { width: 2, type: 'dashed', color: '#86a48c' },
          itemStyle: { color: '#86a48c' },
        },
        {
          name: 'Recall',
          type: 'line',
          data: metrics.map((m) => m.recall),
          smooth: true,
          lineStyle: { width: 2, type: 'dashed', color: '#d9b565' },
          itemStyle: { color: '#d9b565' },
        },
      ],
    })
  }
}

// ── 轮询监控 ──
function startPolling() {
  stopPolling()
  pollTimer = setInterval(() => {
    if (selectedTask.value) {
      fetchMetrics()
    }
  }, 5000) // 每 5 秒轮询一次
}

function stopPolling() {
  if (pollTimer) {
    clearInterval(pollTimer)
    pollTimer = null
  }
}

// ── 创建训练任务 ──
async function createTask() {
  creating.value = true
  try {
    const res = await request.post('/training/start', trainForm.value)
    ElMessage.success(`训练任务已创建：${res.task_uuid}`)
    showCreateDialog.value = false
    await fetchTasks()
    // 自动选中新创建的任务
    if (res.id) {
      const newTask = taskList.value.find((t) => t.id === res.id)
      if (newTask) selectTask(newTask)
    }
  } catch (e) {
    ElMessage.error(e.response?.data?.detail || '创建训练任务失败')
  } finally {
    creating.value = false
  }
}

// ── 停止训练任务 ──
async function stopTask(taskId) {
  try {
    await ElMessageBox.confirm('确定要停止当前训练任务吗？训练进度将被保留。', '确认停止', {
      type: 'warning',
    })
    await request.post(`/training/stop/${taskId}`)
    ElMessage.success('训练任务已停止')
    await fetchTasks()
  } catch (e) {
    if (e !== 'cancel') {
      ElMessage.error('停止训练失败')
    }
  }
}

async function validateModel() {
  if (!selectedTask.value) return
  const taskId = selectedTask.value.id
  validating.value = true
  try {
    const result = await request.post(`/training/validate/${taskId}`, {
      split: 'val',
      conf: 0.001,
      iou: 0.6,
    })
    if (result.status === 'completed' && result.report) {
      validating.value = false
      evalReport.value = result.report
      ElMessage.success('已复用匹配的历史评估结果')
      return
    }
    ElMessage.info('评估任务已启动，正在后台执行...')
    scheduleEvalPoll(taskId)
  } catch (e) {
    validating.value = false
    ElMessage.error(e.response?.data?.detail || '模型评估失败')
  }
}

// ── 评估状态轮询 ──
// 评估在后端异步执行（对 val 集逐张推理，CPU 上可能需要几分钟），
// 启动后每 3 秒轮询一次状态，完成时取回报告
function stopEvalPolling() {
  if (evalPollTimer) {
    clearTimeout(evalPollTimer)
    evalPollTimer = null
  }
}

function scheduleEvalPoll(taskId, delay = 3000) {
  stopEvalPolling()
  evalPollTimer = setTimeout(() => pollEvalStatus(taskId), delay)
}

async function pollEvalStatus(taskId) {
  // 切换任务或离开页面后，废弃旧任务的轮询
  if (!selectedTask.value || selectedTask.value.id !== taskId) return
  try {
    const res = await request.get(`/training/validate/${taskId}/status`, {
      skipGlobalError: true,
    })
    evalPollFailures = 0
    if (res.status === 'running') {
      scheduleEvalPoll(taskId)
      return
    }
    validating.value = false
    if (res.status === 'completed' && res.report) {
      evalReport.value = res.report
      ElMessage.success('模型评估完成')
    } else if (res.status === 'failed') {
      ElMessage.error(res.error || '模型评估失败')
    } else {
      // idle：后端重启导致内存中的评估状态丢失
      ElMessage.warning('评估状态已丢失（后端服务可能重启过），请重新评估')
    }
  } catch (e) {
    evalPollFailures += 1
    if (evalPollFailures >= 5) {
      evalPollFailures = 0
      validating.value = false
      ElMessage.error('评估状态查询失败，请稍后重新评估')
      return
    }
    scheduleEvalPoll(taskId)
  }
}

// 选中任务时恢复评估状态：评估进行中则继续轮询，已完成则直接展示上次报告
async function restoreEvalState(task) {
  if (!isModelReadyTask(task)) return
  try {
    const res = await request.get(`/training/validate/${task.id}/status`, {
      skipGlobalError: true,
    })
    if (!selectedTask.value || selectedTask.value.id !== task.id) return
    if (res.status === 'running') {
      validating.value = true
      scheduleEvalPoll(task.id)
    } else if (res.status === 'completed' && res.report) {
      evalReport.value = res.report
    }
  } catch (e) {
    console.error('恢复评估状态失败', e)
  }
}

async function exportModel() {
  if (!selectedTask.value) return
  stopPolling()
  exporting.value = true
  try {
    const payload = {
      ...exportForm.value,
      version: exportForm.value.version.trim() || null,
      description: exportForm.value.description.trim() || null,
    }
    const result = await request.post(
      `/training/export/${selectedTask.value.id}`,
      payload,
      {
        timeout: 600000,
        skipGlobalError: true,
      },
    )
    const { per_class, ...overall } = result.evaluation
    evalReport.value = { overall, per_class }
    showExportDialog.value = false
    ElMessage.success(result.message)
  } catch (e) {
    const timedOut = e.code === 'ECONNABORTED' || String(e.message || '').toLowerCase().includes('timeout')
    ElMessage.error(
      timedOut
        ? '模型导出处理超时，请稍后刷新训练任务和模型列表确认结果'
        : e.response?.data?.detail || '模型导出失败',
    )
  } finally {
    exporting.value = false
    if (selectedTask.value) {
      startPolling()
    }
  }
}

async function downloadModel() {
  if (!selectedTask.value) return
  try {
    const blob = await request.get(`/training/download/${selectedTask.value.id}`, {
      responseType: 'blob',
    })
    const url = URL.createObjectURL(blob)
    const link = document.createElement('a')
    link.href = url
    link.download = `best_${selectedTask.value.task_uuid}.pt`
    document.body.appendChild(link)
    link.click()
    link.remove()
    URL.revokeObjectURL(url)
  } catch (e) {
    ElMessage.error(e.response?.data?.detail || '模型下载失败')
  }
}

function openPredictDialog() {
  predictResult.value = null
  showPredictDialog.value = true
}

function handleTestFile(uploadFile) {
  predictForm.value.file = uploadFile.raw
  predictResult.value = null
}

function clearTestFile() {
  predictForm.value.file = null
  predictResult.value = null
}

async function runPredict() {
  if (!selectedTask.value || !predictForm.value.file) return
  predicting.value = true
  try {
    const formData = new FormData()
    formData.append('file', predictForm.value.file)
    formData.append('task_id', selectedTask.value.id)
    formData.append('conf', predictForm.value.conf)
    formData.append('iou', predictForm.value.iou)
    predictResult.value = await request.post('/training/predict', formData, {
      headers: { 'Content-Type': 'multipart/form-data' },
    })
    ElMessage.success('测试图片预测完成')
  } catch (e) {
    ElMessage.error(e.response?.data?.detail || '测试图片预测失败')
  } finally {
    predicting.value = false
  }
}

// ── 生命周期 ──
onMounted(() => {
  fetchScenes()
  fetchTasks()
})

onBeforeUnmount(() => {
  stopPolling()
  stopEvalPolling()
  if (lossChart) lossChart.dispose()
  if (mapChart) mapChart.dispose()
})
</script>

<style lang="scss" scoped>
.training-page {
  padding: 20px;
}

.page-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 20px;
}

.page-header h2 {
  margin: 0;
  font-size: 22px;
}

.card-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.monitor-info {
  display: flex;
  gap: 16px;
  font-family: $font-mono;
  font-size: 12px;
  color: $text-secondary;
}

.metric-cards {
  margin-bottom: 8px;
}

.metric-item {
  position: relative;
  text-align: center;
  padding: 8px 0;
  overflow: hidden;

  // 签名：指标卡左上角检测框角标
  &::before {
    content: '';
    position: absolute;
    top: 6px;
    left: 6px;
    width: 10px;
    height: 10px;
    border-top: 2px solid $signal-orange;
    border-left: 2px solid $signal-orange;
    opacity: 0.45;
    pointer-events: none;
  }
}

.metric-value {
  font-family: $font-mono;
  font-variant-numeric: tabular-nums;
  font-size: 20px;
  font-weight: 600;
  color: $text-primary;
}

.metric-label {
  font-size: 12px;
  color: $text-secondary;
  margin-top: 4px;
}

.task-list-card,
.monitor-card {
  margin-bottom: 20px;
}

.model-actions {
  margin-top: 20px;
  border-color: #dcdfe6;
}

.action-hint,
.slider-label {
  color: $text-secondary;
  font-size: 13px;
}

.evaluation-report {
  margin-top: 20px;
}

.evaluation-meta {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 14px;
  color: $text-secondary;
  font-size: 13px;
}

.evaluation-artifacts {
  margin-top: 18px;
}

.evaluation-report h4 {
  margin: 20px 0 12px;
}

.evaluation-metrics {
  row-gap: 12px;
}

.evaluation-metric {
  position: relative;
  padding: 16px;
  text-align: center;
  background: #fafbfc;
  border: 1px solid #edf1f6;
  border-radius: 6px;
  font-family: $font-mono;
  font-variant-numeric: tabular-nums;

  &::before {
    content: '';
    position: absolute;
    top: 6px;
    left: 6px;
    width: 10px;
    height: 10px;
    border-top: 2px solid $signal-orange;
    border-left: 2px solid $signal-orange;
    opacity: 0.45;
    pointer-events: none;
  }
}

.weak-tag {
  margin-left: 8px;
}

:deep(.weak-class-row) {
  --el-table-tr-bg-color: #fdf0ec;
}

.predict-thresholds {
  margin: 20px 0;
}

.slider-label {
  margin-bottom: 4px;
}

.predict-result {
  margin-top: 20px;
}

.annotated-image {
  display: block;
  max-width: 100%;
  max-height: 520px;
  margin: 16px auto;
  border: 1px solid #dcdfe6;
  border-radius: 6px;
}
</style>
