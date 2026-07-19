/**
 * DatasetPanel 组件测试
 *
 * 覆盖：
 *   - 挂载即拉取数据集列表并渲染
 *   - 编辑对话框：英文名只读展示、保存调用双写接口并触发 scenes-changed
 *   - 评估：打开抽屉即请求报告
 */
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, shallowMount } from '@vue/test-utils'

const getDatasets = vi.fn()
const updateDatasetNames = vi.fn()
const evaluateDataset = vi.fn()

vi.mock('@/api/datasets', () => ({
  getDatasets: (...args) => getDatasets(...args),
  updateDatasetNames: (...args) => updateDatasetNames(...args),
  uploadDataset: vi.fn(),
  commitDatasetUpload: vi.fn(),
  evaluateDataset: (...args) => evaluateDataset(...args),
}))

vi.mock('element-plus', () => ({
  ElMessage: { success: vi.fn(), warning: vi.fn(), error: vi.fn() },
  ElMessageBox: { confirm: vi.fn() },
}))

import DatasetPanel from '@/components/training/DatasetPanel.vue'

const SAMPLE_ITEM = {
  name: 'fdd',
  ready: true,
  scene: { id: 1, display_name: '织物缺陷检测', category: 'industry', is_active: true },
  class_names: ['hole', 'stain'],
  class_names_cn: { hole: '破洞', stain: '污渍' },
  image_counts: { train: 10, val: 2, test: 1 },
  total_images: 13,
  has_report: false,
}

function mountPanel() {
  return shallowMount(DatasetPanel, {
    global: {
      stubs: {
        ElTable: { template: '<div />' },
        ElTableColumn: { template: '<div />' },
        ElCard: { template: '<div><slot name="header" /><slot /></div>' },
        ElDialog: { template: '<div><slot /><slot name="footer" /></div>' },
        ElDrawer: { template: '<div><slot /></div>' },
      },
    },
  })
}

describe('DatasetPanel', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    getDatasets.mockResolvedValue({ items: [SAMPLE_ITEM] })
  })

  it('挂载即拉取数据集列表', async () => {
    const wrapper = mountPanel()
    await flushPromises()
    expect(getDatasets).toHaveBeenCalledTimes(1)
    expect(wrapper.vm.datasets).toHaveLength(1)
    expect(wrapper.vm.datasets[0].name).toBe('fdd')
  })

  it('编辑对话框预填场景名与中文名，英文名保持只读列表', async () => {
    const wrapper = mountPanel()
    await flushPromises()
    wrapper.vm.openEditDialog(SAMPLE_ITEM)
    expect(wrapper.vm.editVisible).toBe(true)
    expect(wrapper.vm.editForm.displayName).toBe('织物缺陷检测')
    expect(wrapper.vm.editForm.classNames).toEqual(['hole', 'stain'])
    expect(wrapper.vm.editForm.classNamesCn).toEqual({ hole: '破洞', stain: '污渍' })
  })

  it('保存名称调用双写接口并触发 scenes-changed', async () => {
    updateDatasetNames.mockResolvedValue({ scene_synced: true })
    const wrapper = mountPanel()
    await flushPromises()
    wrapper.vm.openEditDialog(SAMPLE_ITEM)
    wrapper.vm.editForm.classNamesCn.hole = '新破洞'
    await wrapper.vm.submitEdit()
    await flushPromises()
    expect(updateDatasetNames).toHaveBeenCalledWith('fdd', {
      displayName: '织物缺陷检测',
      classNamesCn: { hole: '新破洞', stain: '污渍' },
    })
    expect(wrapper.emitted('scenes-changed')).toHaveLength(1)
    // 保存后刷新列表
    expect(getDatasets).toHaveBeenCalledTimes(2)
  })

  it('打开评估抽屉即请求报告', async () => {
    evaluateDataset.mockResolvedValue({
      passed: true,
      cached: false,
      generated_at: '2026-07-19T12:00:00',
      summary: { total_images: 13, total_annotations: 20, avg_annotations_per_image: 1.5, empty_labels: 0 },
      splits: { train: { images: 10, labels: 10, annotations: 16 } },
      class_distribution: [],
      issues: [],
      suggestions: ['数据集结构与标注质量良好，可直接用于训练'],
    })
    const wrapper = mountPanel()
    await flushPromises()
    wrapper.vm.openEvaluate(SAMPLE_ITEM)
    await flushPromises()
    expect(evaluateDataset).toHaveBeenCalledWith('fdd', { force: false })
    expect(wrapper.vm.reportVisible).toBe(true)
    expect(wrapper.vm.report.passed).toBe(true)
    expect(wrapper.vm.splitRows[0]).toEqual({ split: 'train', images: 10, labels: 10, annotations: 16 })
  })

  it('接口失败时静默保持空列表', async () => {
    getDatasets.mockRejectedValue(new Error('network'))
    const wrapper = mountPanel()
    await flushPromises()
    expect(wrapper.vm.datasets).toEqual([])
  })
})
