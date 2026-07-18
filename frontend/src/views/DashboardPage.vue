<template>
  <div v-loading="loading" class="dashboard-page">
    <div class="page-header">
      <div>
        <h2>数据看板</h2>
        <p>灵活查看全厂检测任务、缺陷类别与趋势</p>
      </div>
    </div>

    <el-card shadow="never" class="filter-bar">
      <div class="filter-row">
        <div class="filter-item">
          <span class="filter-label">时间</span>
          <el-radio-group v-model="preset" @change="handlePresetChange">
            <el-radio-button :value="7">近 7 天</el-radio-button>
            <el-radio-button :value="30">近 30 天</el-radio-button>
            <el-radio-button :value="90">近 90 天</el-radio-button>
            <el-radio-button value="custom">自定义</el-radio-button>
          </el-radio-group>
          <el-date-picker
            v-if="preset === 'custom'"
            v-model="dateRange"
            type="daterange"
            value-format="YYYY-MM-DD"
            range-separator="至"
            start-placeholder="开始日期"
            end-placeholder="结束日期"
            :clearable="false"
            @change="handleDateRangeChange"
          />
        </div>
        <div class="filter-item">
          <span class="filter-label">缺陷</span>
          <el-select
            v-model="selectedDefects"
            multiple
            filterable
            collapse-tags
            collapse-tags-tooltip
            placeholder="全部缺陷"
            class="defect-select"
            @change="loadAllData"
          >
            <el-option
              v-for="option in defectOptions"
              :key="option.name"
              :label="`${option.name_cn}（${option.count}）`"
              :value="option.name"
            />
          </el-select>
        </div>
      </div>
    </el-card>

    <el-row :gutter="16" class="stat-cards">
      <el-col
        v-for="card in statCards"
        :key="card.key"
        :xs="24"
        :sm="12"
        :lg="6"
      >
        <el-card shadow="hover" class="stat-card">
          <div class="stat-icon" :style="{ background: card.background }">
            <el-icon :size="28" :color="card.color">
              <component :is="card.icon" />
            </el-icon>
          </div>
          <div class="stat-info">
            <div class="stat-value">
              {{ card.format(stats[card.key]) }}<span v-if="card.unit" class="unit">{{ card.unit }}</span>
            </div>
            <div class="stat-label">{{ card.label }}</div>
            <div
              class="stat-growth"
              :class="growthClass(card.growthKey, card.inverse)"
            >
              {{ formatGrowth(stats.growth?.[card.growthKey]) }}
            </div>
          </div>
        </el-card>
      </el-col>
    </el-row>

    <el-row :gutter="16" class="chart-row">
      <el-col :xs="24" :xl="16">
        <el-card shadow="hover">
          <template #header>
            <div class="card-header">
              <span>{{ defectFilterActive ? '所选缺陷每日趋势' : '每日检测趋势' }}</span>
            </div>
          </template>
          <div ref="trendChartRef" class="chart-container" />
        </el-card>
      </el-col>
      <el-col :xs="24" :xl="8">
        <el-card shadow="hover">
          <template #header>类别分布</template>
          <div ref="classChartRef" class="chart-container" />
        </el-card>
      </el-col>
    </el-row>

    <el-row :gutter="16" class="chart-row">
      <el-col :xs="24">
        <el-card shadow="hover">
          <template #header>
            <div class="card-header">
              <span>缺陷类别趋势对比</span>
              <span class="card-hint">
                {{ defectFilterActive ? '按所选缺陷拆分' : '默认展示目标数最多的类别' }}
              </span>
            </div>
          </template>
          <div ref="defectTrendChartRef" class="chart-container chart-container--tall" />
        </el-card>
      </el-col>
    </el-row>

    <el-row :gutter="16" class="chart-row">
      <el-col :xs="24" :xl="12">
        <el-card shadow="hover">
          <template #header>场景分布</template>
          <div ref="sceneChartRef" class="chart-container" />
        </el-card>
      </el-col>
      <el-col :xs="24" :xl="12">
        <el-card shadow="hover">
          <template #header>任务类型分布</template>
          <div ref="typeChartRef" class="chart-container" />
        </el-card>
      </el-col>
    </el-row>
  </div>
</template>

<script setup>
import {
  getClassDistribution,
  getDefectOptions,
  getDefectTrend,
  getSceneDistribution,
  getStatistics,
  getTrend,
  getTypeDistribution,
} from '@/api/dashboard'
import { Aim, Document, PictureFilled, Timer } from '@element-plus/icons-vue'
import * as echarts from 'echarts'
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'

const preset = ref(30)
const dateRange = ref(null)
const selectedDefects = ref([])
const defectOptions = ref([])
const loading = ref(false)
const stats = ref({
  total_tasks: 0,
  total_images: 0,
  total_objects: 0,
  avg_inference_time: 0,
  growth: {},
})

const trendChartRef = ref(null)
const classChartRef = ref(null)
const defectTrendChartRef = ref(null)
const sceneChartRef = ref(null)
const typeChartRef = ref(null)
let trendChart = null
let classChart = null
let defectTrendChart = null
let sceneChart = null
let typeChart = null

const defectFilterActive = computed(() => selectedDefects.value.length > 0)

/** 把当前筛选状态转成 API 参数：自定义区间优先，否则用天数预设。 */
function currentQuery() {
  const query = { classNames: selectedDefects.value }
  if (preset.value === 'custom' && dateRange.value?.length === 2) {
    query.start = dateRange.value[0]
    query.end = dateRange.value[1]
  } else if (preset.value !== 'custom') {
    query.days = preset.value
  } else {
    // 自定义但尚未选日期时，回退到 30 天避免空查询。
    query.days = 30
  }
  return query
}

const statCards = computed(() => [
  {
    key: 'total_tasks',
    growthKey: 'tasks',
    label: '检测任务',
    icon: Document,
    color: '#24314f',
    background: '#e9edf6',
    format: formatNumber,
  },
  {
    key: 'total_images',
    growthKey: 'images',
    label: '处理图片',
    icon: PictureFilled,
    color: '#6b8f71',
    background: '#eef4ef',
    format: formatNumber,
  },
  {
    key: 'total_objects',
    growthKey: 'objects',
    label: defectFilterActive.value ? '缺陷目标' : '检测目标',
    icon: Aim,
    color: '#e8613c',
    background: '#fdece6',
    format: formatNumber,
  },
  {
    key: 'avg_inference_time',
    growthKey: 'inference_time',
    label: '平均耗时',
    icon: Timer,
    color: '#d9a441',
    background: '#faf3e4',
    format: (value) => Number(value || 0).toFixed(2),
    unit: 'ms',
    inverse: true,
  },
])

function formatNumber(value) {
  const number = Number(value || 0)
  if (number >= 10000) return `${(number / 10000).toFixed(1)}w`
  if (number >= 1000) return `${(number / 1000).toFixed(1)}k`
  return String(number)
}

function formatGrowth(value) {
  if (value === undefined || value === null) return '暂无环比'
  if (value > 0) return `+${value}%`
  if (value < 0) return `${value}%`
  return '持平'
}

function growthClass(key, inverse = false) {
  const value = stats.value.growth?.[key]
  if (!value) return 'growth-flat'
  if (inverse) return value < 0 ? 'growth-up' : 'growth-down'
  return value > 0 ? 'growth-up' : 'growth-down'
}

function emptyGraphic(isEmpty) {
  return isEmpty
    ? [{
        type: 'text',
        left: 'center',
        top: 'middle',
        style: { text: '暂无数据', fill: '#909399', fontSize: 14 },
      }]
    : []
}

function handlePresetChange(value) {
  if (value === 'custom') {
    // 切到自定义但未选日期时不立即请求，等用户选完区间。
    if (dateRange.value?.length === 2) loadAllData()
    return
  }
  loadAllData()
}

function handleDateRangeChange() {
  if (dateRange.value?.length === 2) loadAllData()
}

/** 拉取缺陷下拉选项（不受已选缺陷影响，跟随时间窗口）。 */
async function loadDefectOptions(query) {
  try {
    const { classNames, ...rest } = query
    const result = await getDefectOptions(rest)
    defectOptions.value = result.options || []
    // 时间窗口变化后，清理不再存在的已选缺陷。
    const available = new Set(defectOptions.value.map((item) => item.name))
    selectedDefects.value = selectedDefects.value.filter((name) => available.has(name))
  } catch (error) {
    console.error('[缺陷选项加载失败]', error)
  }
}

async function loadAllData() {
  loading.value = true
  try {
    const query = currentQuery()
    const [statistics, trend, defectTrend, classDist, sceneDist, typeDist] =
      await Promise.all([
        getStatistics(query),
        getTrend(query),
        getDefectTrend(query),
        getClassDistribution(query),
        getSceneDistribution(query),
        getTypeDistribution(query),
      ])
    stats.value = statistics
    renderTrendChart(trend.trend || [])
    renderDefectTrendChart(defectTrend)
    renderClassChart(classDist.distribution || [])
    renderSceneChart(sceneDist.distribution || [])
    renderTypeChart(typeDist.distribution || [])
  } catch (error) {
    console.error('[看板数据加载失败]', error)
  } finally {
    loading.value = false
  }
}

function renderTrendChart(trend) {
  trendChart ||= echarts.init(trendChartRef.value)
  trendChart.setOption({
    graphic: emptyGraphic(trend.length === 0),
    tooltip: { trigger: 'axis', axisPointer: { type: 'cross' } },
    legend: { data: ['检测任务', defectFilterActive.value ? '缺陷目标' : '检测目标'], bottom: 0 },
    grid: { left: 50, right: 50, top: 24, bottom: 44 },
    xAxis: {
      type: 'category',
      data: trend.map((item) => item.date.slice(5)),
      axisLabel: { fontSize: 11 },
    },
    yAxis: [
      { type: 'value', name: '任务数', minInterval: 1 },
      { type: 'value', name: '目标数', minInterval: 1 },
    ],
    series: [
      {
        name: '检测任务',
        type: 'line',
        smooth: true,
        data: trend.map((item) => item.task_count),
        itemStyle: { color: '#24314f' },
        areaStyle: {
          color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
            { offset: 0, color: 'rgba(36,49,79,0.28)' },
            { offset: 1, color: 'rgba(36,49,79,0.02)' },
          ]),
        },
      },
      {
        name: defectFilterActive.value ? '缺陷目标' : '检测目标',
        type: 'line',
        smooth: true,
        yAxisIndex: 1,
        data: trend.map((item) => item.object_count),
        itemStyle: { color: '#e8613c' },
      },
    ],
  }, true)
}

function renderDefectTrendChart(payload) {
  const dates = payload?.dates || []
  const series = payload?.series || []
  defectTrendChart ||= echarts.init(defectTrendChartRef.value)
  defectTrendChart.setOption({
    graphic: emptyGraphic(series.length === 0),
    color: ['#e8613c', '#24314f', '#6b8f71', '#d9a441', '#7a8296', '#a06a8c', '#c4402a', '#4a5b8a'],
    tooltip: { trigger: 'axis', axisPointer: { type: 'cross' } },
    legend: {
      type: 'scroll',
      bottom: 0,
      data: series.map((item) => item.name_cn),
    },
    grid: { left: 50, right: 30, top: 24, bottom: 48 },
    xAxis: {
      type: 'category',
      boundaryGap: false,
      data: dates.map((date) => date.slice(5)),
      axisLabel: { fontSize: 11 },
    },
    yAxis: { type: 'value', name: '缺陷数', minInterval: 1 },
    series: series.map((item) => ({
      name: item.name_cn,
      type: 'line',
      smooth: true,
      showSymbol: false,
      emphasis: { focus: 'series' },
      data: item.data,
    })),
  }, true)
}

function renderClassChart(distribution) {
  classChart ||= echarts.init(classChartRef.value)
  classChart.setOption({
    graphic: emptyGraphic(distribution.length === 0),
    color: ['#e8613c', '#24314f', '#6b8f71', '#d9a441', '#7a8296', '#a06a8c', '#c4402a', '#4a5b8a'],
    tooltip: { trigger: 'item', formatter: '{b}: {c} ({d}%)' },
    legend: { type: 'scroll', orient: 'vertical', right: 8, top: 20, bottom: 20 },
    series: [{
      type: 'pie',
      radius: '65%',
      center: ['38%', '50%'],
      data: distribution.map((item) => ({ name: item.name_cn || item.name, value: item.value })),
      label: { formatter: '{b}\n{d}%' },
    }],
  }, true)
}

function renderSceneChart(distribution) {
  sceneChart ||= echarts.init(sceneChartRef.value)
  sceneChart.setOption({
    graphic: emptyGraphic(distribution.length === 0),
    tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' } },
    grid: { left: 90, right: 36, top: 20, bottom: 30 },
    xAxis: { type: 'value', minInterval: 1 },
    yAxis: { type: 'category', data: distribution.map((item) => item.name) },
    series: [{
      type: 'bar',
      data: distribution.map((item) => item.value),
      barMaxWidth: 32,
      label: { show: true, position: 'right' },
      itemStyle: {
        borderRadius: [0, 4, 4, 0],
        color: new echarts.graphic.LinearGradient(0, 0, 1, 0, [
          { offset: 0, color: '#24314f' },
          { offset: 1, color: '#4a5b8a' },
        ]),
      },
    }],
  }, true)
}

function renderTypeChart(distribution) {
  typeChart ||= echarts.init(typeChartRef.value)
  typeChart.setOption({
    graphic: emptyGraphic(distribution.length === 0),
    color: ['#e8613c', '#24314f', '#6b8f71', '#d9a441', '#7a8296', '#a06a8c'],
    tooltip: { trigger: 'item', formatter: '{b}: {c} ({d}%)' },
    legend: { bottom: 0, type: 'scroll' },
    series: [{
      type: 'pie',
      radius: ['40%', '65%'],
      center: ['50%', '45%'],
      data: distribution,
      label: { formatter: '{b}\n{d}%' },
    }],
  }, true)
}

function handleResize() {
  trendChart?.resize()
  classChart?.resize()
  defectTrendChart?.resize()
  sceneChart?.resize()
  typeChart?.resize()
}

onMounted(async () => {
  await loadDefectOptions(currentQuery())
  await loadAllData()
  window.addEventListener('resize', handleResize)
})

onBeforeUnmount(() => {
  window.removeEventListener('resize', handleResize)
  trendChart?.dispose()
  classChart?.dispose()
  defectTrendChart?.dispose()
  sceneChart?.dispose()
  typeChart?.dispose()
})
</script>

<style lang="scss" scoped>
.dashboard-page { min-height: 100%; }
.page-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  margin-bottom: 16px;
  h2 { margin: 0 0 4px; }
  p { margin: 0; color: $text-secondary; font-size: 14px; }
}
.filter-bar {
  margin-bottom: 16px;
  :deep(.el-card__body) { padding: 16px 20px; }
}
.filter-row {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 24px;
}
.filter-item {
  display: flex;
  align-items: center;
  gap: 12px;
}
.filter-label { color: $text-secondary; font-size: 14px; white-space: nowrap; }
.defect-select { min-width: 260px; }
.card-header {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
}
.card-hint { color: $text-secondary; font-size: 12px; }
.stat-cards { row-gap: 16px; margin-bottom: 16px; }
.stat-card {
  position: relative;
  overflow: hidden;
}
.stat-card::before {
  // 签名：卡片左上角检测框角标
  content: '';
  position: absolute;
  top: 8px;
  left: 8px;
  width: 12px;
  height: 12px;
  border-top: 2px solid $signal-orange;
  border-left: 2px solid $signal-orange;
  opacity: 0.5;
  pointer-events: none;
}
.stat-card :deep(.el-card__body) {
  display: flex;
  align-items: center;
  gap: 16px;
  min-height: 104px;
  padding: 20px;
}
.stat-icon {
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
  width: 56px;
  height: 56px;
  border-radius: 12px;
}
.stat-info { min-width: 0; }
.stat-value {
  color: $text-primary;
  font-family: $font-mono;
  font-variant-numeric: tabular-nums;
  font-size: 27px;
  font-weight: 600;
  line-height: 1.2;
}
.unit { margin-left: 3px; color: $text-secondary; font-size: 13px; font-weight: 400; }
.stat-label { margin-top: 3px; color: $text-secondary; font-size: 13px; }
.stat-growth { margin-top: 4px; font-family: $font-mono; font-size: 12px; }
.growth-up { color: $loom-green; &::before { content: '↑ '; } }
.growth-down { color: $danger-color; &::before { content: '↓ '; } }
.growth-flat { color: $text-secondary; }
.chart-row { row-gap: 16px; margin-bottom: 16px; }
.chart-container { width: 100%; height: 320px; }
.chart-container--tall { height: 360px; }
@media (max-width: 768px) {
  .page-header { align-items: flex-start; flex-direction: column; }
  .filter-row { gap: 16px; }
  .filter-item { width: 100%; }
  .defect-select { flex: 1; min-width: 0; }
}
</style>
