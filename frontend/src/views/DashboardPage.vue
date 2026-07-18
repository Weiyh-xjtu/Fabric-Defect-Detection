<template>
  <div v-loading="loading" class="dashboard-page">
    <div class="page-header">
      <div>
        <h2>数据看板</h2>
        <p>灵活查看全厂检测任务、缺陷类别与趋势</p>
      </div>
      <el-button :icon="Download" @click="exportSummary">导出汇总</el-button>
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
              <div class="card-actions">
                <el-radio-group v-model="viewModes.trend" size="small" @change="handleViewChange">
                  <el-radio-button value="chart">图表</el-radio-button>
                  <el-radio-button value="table">表格</el-radio-button>
                </el-radio-group>
                <el-button size="small" :icon="Download" text bg @click="exportTable('trend')">CSV</el-button>
              </div>
            </div>
          </template>
          <div v-show="viewModes.trend === 'chart'" ref="trendChartRef" class="chart-container" />
          <el-table
            v-if="viewModes.trend === 'table'"
            :data="tableConfigs.trend.rows"
            size="small"
            border
            max-height="320"
            class="data-table"
          >
            <el-table-column
              v-for="col in tableConfigs.trend.columns"
              :key="col.prop"
              :prop="col.prop"
              :label="col.label"
              :width="col.width"
              show-overflow-tooltip
            />
          </el-table>
        </el-card>
      </el-col>
      <el-col :xs="24" :xl="8">
        <el-card shadow="hover">
          <template #header>
            <div class="card-header">
              <span>类别分布</span>
              <div class="card-actions">
                <el-radio-group v-model="viewModes.classDist" size="small" @change="handleViewChange">
                  <el-radio-button value="chart">图表</el-radio-button>
                  <el-radio-button value="table">表格</el-radio-button>
                </el-radio-group>
                <el-button size="small" :icon="Download" text bg @click="exportTable('classDist')">CSV</el-button>
              </div>
            </div>
          </template>
          <div v-show="viewModes.classDist === 'chart'" ref="classChartRef" class="chart-container" />
          <el-table
            v-if="viewModes.classDist === 'table'"
            :data="tableConfigs.classDist.rows"
            size="small"
            border
            max-height="320"
            class="data-table"
          >
            <el-table-column
              v-for="col in tableConfigs.classDist.columns"
              :key="col.prop"
              :prop="col.prop"
              :label="col.label"
              :width="col.width"
              show-overflow-tooltip
            />
          </el-table>
        </el-card>
      </el-col>
    </el-row>

    <el-row :gutter="16" class="chart-row">
      <el-col :xs="24">
        <el-card shadow="hover">
          <template #header>
            <div class="card-header">
              <span>缺陷类别趋势对比</span>
              <div class="card-actions">
                <span class="card-hint">
                  {{ defectFilterActive ? '按所选缺陷拆分' : '默认展示目标数最多的类别' }}
                </span>
                <el-radio-group v-model="viewModes.defectTrend" size="small" @change="handleViewChange">
                  <el-radio-button value="chart">图表</el-radio-button>
                  <el-radio-button value="table">表格</el-radio-button>
                </el-radio-group>
                <el-button size="small" :icon="Download" text bg @click="exportTable('defectTrend')">CSV</el-button>
              </div>
            </div>
          </template>
          <div
            v-show="viewModes.defectTrend === 'chart'"
            ref="defectTrendChartRef"
            class="chart-container chart-container--tall"
          />
          <el-table
            v-if="viewModes.defectTrend === 'table'"
            :data="tableConfigs.defectTrend.rows"
            size="small"
            border
            max-height="360"
            class="data-table"
          >
            <el-table-column
              v-for="col in tableConfigs.defectTrend.columns"
              :key="col.prop"
              :prop="col.prop"
              :label="col.label"
              :width="col.width"
              show-overflow-tooltip
            />
          </el-table>
        </el-card>
      </el-col>
    </el-row>

    <el-row :gutter="16" class="chart-row">
      <el-col :xs="24" :xl="12">
        <el-card shadow="hover">
          <template #header>
            <div class="card-header">
              <span>场景分布</span>
              <div class="card-actions">
                <el-radio-group v-model="viewModes.sceneDist" size="small" @change="handleViewChange">
                  <el-radio-button value="chart">图表</el-radio-button>
                  <el-radio-button value="table">表格</el-radio-button>
                </el-radio-group>
                <el-button size="small" :icon="Download" text bg @click="exportTable('sceneDist')">CSV</el-button>
              </div>
            </div>
          </template>
          <div v-show="viewModes.sceneDist === 'chart'" ref="sceneChartRef" class="chart-container" />
          <el-table
            v-if="viewModes.sceneDist === 'table'"
            :data="tableConfigs.sceneDist.rows"
            size="small"
            border
            max-height="320"
            class="data-table"
          >
            <el-table-column
              v-for="col in tableConfigs.sceneDist.columns"
              :key="col.prop"
              :prop="col.prop"
              :label="col.label"
              :width="col.width"
              show-overflow-tooltip
            />
          </el-table>
        </el-card>
      </el-col>
      <el-col :xs="24" :xl="12">
        <el-card shadow="hover">
          <template #header>
            <div class="card-header">
              <span>任务类型分布</span>
              <div class="card-actions">
                <el-radio-group v-model="viewModes.typeDist" size="small" @change="handleViewChange">
                  <el-radio-button value="chart">图表</el-radio-button>
                  <el-radio-button value="table">表格</el-radio-button>
                </el-radio-group>
                <el-button size="small" :icon="Download" text bg @click="exportTable('typeDist')">CSV</el-button>
              </div>
            </div>
          </template>
          <div v-show="viewModes.typeDist === 'chart'" ref="typeChartRef" class="chart-container" />
          <el-table
            v-if="viewModes.typeDist === 'table'"
            :data="tableConfigs.typeDist.rows"
            size="small"
            border
            max-height="320"
            class="data-table"
          >
            <el-table-column
              v-for="col in tableConfigs.typeDist.columns"
              :key="col.prop"
              :prop="col.prop"
              :label="col.label"
              :width="col.width"
              show-overflow-tooltip
            />
          </el-table>
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
import { exportCsv } from '@/utils/csv'
import { Aim, Document, Download, PictureFilled, Timer } from '@element-plus/icons-vue'
import * as echarts from 'echarts'
import { ElMessage } from 'element-plus'
import { computed, nextTick, onBeforeUnmount, onMounted, reactive, ref } from 'vue'

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

// 各卡片原始数据，图表和表格共用同一份。
const trendData = ref([])
const defectTrendData = ref({ dates: [], series: [] })
const classDistData = ref([])
const sceneDistData = ref([])
const typeDistData = ref([])

// 各卡片的展示模式：chart | table。
const viewModes = reactive({
  trend: 'chart',
  defectTrend: 'chart',
  classDist: 'chart',
  sceneDist: 'chart',
  typeDist: 'chart',
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
    color: '#6f7f9b',
    background: '#f0f3f8',
    format: formatNumber,
  },
  {
    key: 'total_images',
    growthKey: 'images',
    label: '处理图片',
    icon: PictureFilled,
    color: '#86a48c',
    background: '#f1f6f2',
    format: formatNumber,
  },
  {
    key: 'total_objects',
    growthKey: 'objects',
    label: defectFilterActive.value ? '缺陷目标' : '检测目标',
    icon: Aim,
    color: '#df6b4e',
    background: '#fdf0ec',
    format: formatNumber,
  },
  {
    key: 'avg_inference_time',
    growthKey: 'inference_time',
    label: '平均耗时',
    icon: Timer,
    color: '#d9b565',
    background: '#fbf6e8',
    format: (value) => Number(value || 0).toFixed(2),
    unit: 'ms',
    inverse: true,
  },
])

/** 给分布数据补充占比列。 */
function withShare(rows) {
  const total = rows.reduce((sum, row) => sum + Number(row.value || 0), 0)
  return rows.map((row) => ({
    ...row,
    share: total ? `${((Number(row.value || 0) / total) * 100).toFixed(1)}%` : '0%',
  }))
}

/** 各卡片表格的列定义与行数据（与图表同源）。 */
const tableConfigs = computed(() => {
  const defectSeries = defectTrendData.value.series || []
  return {
    trend: {
      title: defectFilterActive.value ? '所选缺陷每日趋势' : '每日检测趋势',
      columns: [
        { prop: 'date', label: '日期', width: 110 },
        { prop: 'task_count', label: '任务数' },
        { prop: 'image_count', label: '图片数' },
        { prop: 'object_count', label: defectFilterActive.value ? '缺陷目标数' : '目标数' },
      ],
      rows: trendData.value,
    },
    defectTrend: {
      title: '缺陷类别趋势对比',
      columns: [
        { prop: 'date', label: '日期', width: 110 },
        ...defectSeries.map((series, index) => ({
          prop: `c${index}`,
          label: series.name_cn || series.name,
        })),
      ],
      rows: (defectTrendData.value.dates || []).map((date, dateIndex) => {
        const row = { date }
        defectSeries.forEach((series, index) => {
          row[`c${index}`] = series.data?.[dateIndex] ?? 0
        })
        return row
      }),
    },
    classDist: {
      title: '类别分布',
      columns: [
        { prop: 'name_cn', label: '缺陷类别' },
        { prop: 'name', label: '标签名' },
        { prop: 'value', label: '目标数' },
        { prop: 'share', label: '占比', width: 80 },
      ],
      rows: withShare(classDistData.value),
    },
    sceneDist: {
      title: '场景分布',
      columns: [
        { prop: 'name', label: '场景' },
        { prop: 'value', label: '任务数' },
        { prop: 'share', label: '占比', width: 80 },
      ],
      rows: withShare(sceneDistData.value),
    },
    typeDist: {
      title: '任务类型分布',
      columns: [
        { prop: 'name', label: '任务类型' },
        { prop: 'value', label: '任务数' },
        { prop: 'share', label: '占比', width: 80 },
      ],
      rows: withShare(typeDistData.value),
    },
  }
})

/** 当前时间窗口的文件名后缀。 */
function rangeLabel() {
  if (preset.value === 'custom' && dateRange.value?.length === 2) {
    return `${dateRange.value[0]}_${dateRange.value[1]}`
  }
  const days = preset.value === 'custom' ? 30 : preset.value
  return `近${days}天`
}

/** 导出指定卡片的表格数据为 CSV。 */
function exportTable(key) {
  const config = tableConfigs.value[key]
  if (!config.rows.length) {
    ElMessage.warning('暂无数据可导出')
    return
  }
  exportCsv(
    `数据看板-${config.title}-${rangeLabel()}`,
    config.columns.map((col) => col.label),
    config.rows.map((row) => config.columns.map((col) => row[col.prop])),
  )
}

/** 导出顶部统计卡片的汇总数据（含环比）。 */
function exportSummary() {
  const growth = stats.value.growth || {}
  const formatRate = (value) =>
    value === undefined || value === null ? '' : `${value}%`
  exportCsv(
    `数据看板-汇总统计-${rangeLabel()}`,
    [
      '统计周期', '检测任务', '处理图片',
      defectFilterActive.value ? '缺陷目标' : '检测目标', '平均耗时(ms)',
      '任务环比', '图片环比', '目标环比', '耗时环比',
    ],
    [[
      rangeLabel(),
      stats.value.total_tasks,
      stats.value.total_images,
      stats.value.total_objects,
      Number(stats.value.avg_inference_time || 0).toFixed(2),
      formatRate(growth.tasks),
      formatRate(growth.images),
      formatRate(growth.objects),
      formatRate(growth.inference_time),
    ]],
  )
}

/** 切回图表模式后容器由隐藏变可见，需要重新计算尺寸。 */
async function handleViewChange() {
  await nextTick()
  handleResize()
}

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
        style: { text: '暂无数据', fill: '#9aa3b2', fontSize: 14 },
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
    trendData.value = trend.trend || []
    defectTrendData.value = {
      dates: defectTrend?.dates || [],
      series: defectTrend?.series || [],
    }
    classDistData.value = classDist.distribution || []
    sceneDistData.value = sceneDist.distribution || []
    typeDistData.value = typeDist.distribution || []
    renderTrendChart(trendData.value)
    renderDefectTrendChart(defectTrendData.value)
    renderClassChart(classDistData.value)
    renderSceneChart(sceneDistData.value)
    renderTypeChart(typeDistData.value)
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
        itemStyle: { color: '#6f7f9b' },
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
        itemStyle: { color: '#df6b4e' },
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
    color: ['#df6b4e', '#6f7f9b', '#86a48c', '#d9b565', '#a6afbf', '#c5a3bb', '#d98067', '#9aa8c8'],
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
    color: ['#df6b4e', '#6f7f9b', '#86a48c', '#d9b565', '#a6afbf', '#c5a3bb', '#d98067', '#9aa8c8'],
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
          { offset: 0, color: '#6f7f9b' },
          { offset: 1, color: '#9aa8c8' },
        ]),
      },
    }],
  }, true)
}

function renderTypeChart(distribution) {
  typeChart ||= echarts.init(typeChartRef.value)
  typeChart.setOption({
    graphic: emptyGraphic(distribution.length === 0),
    color: ['#df6b4e', '#6f7f9b', '#86a48c', '#d9b565', '#a6afbf', '#c5a3bb'],
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
.dashboard-page {
  min-height: 100%;
  padding: 20px;
}
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
  flex-wrap: wrap;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}
.card-actions {
  display: flex;
  align-items: center;
  gap: 8px;
}
.card-hint { color: $text-secondary; font-size: 12px; }
.data-table {
  width: 100%;
  font-variant-numeric: tabular-nums;
}
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
