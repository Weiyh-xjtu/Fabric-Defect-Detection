import { beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import ElementPlus, { ElMessageBox } from 'element-plus'

const getModelVersions = vi.fn()
const getCurrentModel = vi.fn()
const activateModel = vi.fn()
const evaluateModel = vi.fn()
const getModelEvaluation = vi.fn()

vi.mock('@/api/models', () => ({
  getModelVersions: (...args) => getModelVersions(...args),
  getCurrentModel: (...args) => getCurrentModel(...args),
  activateModel: (...args) => activateModel(...args),
  evaluateModel: (...args) => evaluateModel(...args),
  getModelEvaluation: (...args) => getModelEvaluation(...args),
  testModel: vi.fn(),
}))

vi.mock('@/utils/request', () => ({
  default: {
    get: vi.fn(() => Promise.resolve({
      items: [{ id: 2, display_name: '织物缺陷检测' }],
    })),
  },
  getApiErrorMessage: vi.fn((_response, fallback) => fallback),
}))

import ModelManagementPage from '@/views/ModelManagementPage.vue'

const current = {
  id: 1,
  scene_id: 2,
  scene_name: '织物缺陷检测',
  training_task_id: 6,
  version: 'v2.0.0',
  model_name: 'fabric-v2',
  model_type: 'yolo11n',
  status: 'active',
  map50: 0.82,
  map50_95: 0.51,
  file_exists: true,
  detection_task_count: 12,
  is_global_default: true,
  created_at: '2026-07-17T10:00:00',
}

const candidate = {
  ...current,
  id: 2,
  version: 'v3.0.0',
  model_name: 'fabric-v3',
  is_global_default: false,
  detection_task_count: 0,
}

function mountPage() {
  return mount(ModelManagementPage, { global: { plugins: [ElementPlus] } })
}

describe('ModelManagementPage', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    getCurrentModel.mockResolvedValue({ ...current })
    getModelVersions.mockResolvedValue({ items: [{ ...current }, { ...candidate }] })
    activateModel.mockResolvedValue({ message: '切换成功', model: { ...candidate, is_global_default: true } })
    evaluateModel.mockResolvedValue({ status: 'running' })
    getModelEvaluation.mockResolvedValue({
      status: 'completed',
      report: {
        split: 'val',
        overall: { precision: 0.8, recall: 0.7, map50: 0.82, map50_95: 0.51 },
        per_class: { defect: { ap50: 0.82, ap50_95: 0.51, instances: 10 } },
      },
    })
  })

  it('展示当前模型与全部版本，并可切换全局模型', async () => {
    vi.spyOn(ElMessageBox, 'confirm').mockResolvedValue('confirm')
    const wrapper = mountPage()
    await flushPromises()

    expect(wrapper.text()).toContain('当前全局检测模型')
    expect(wrapper.text()).toContain('v2.0.0')
    expect(wrapper.text()).toContain('v3.0.0')

    const switchButtons = wrapper.findAll('button').filter((item) => item.text().includes('设为全局'))
    await switchButtons[1].trigger('click')
    await flushPromises()

    expect(activateModel).toHaveBeenCalledWith(2)
    expect(getCurrentModel).toHaveBeenCalledTimes(2)
    expect(getModelVersions).toHaveBeenCalledTimes(2)
  })

  it('从模型版本行启动评估并展示结果', async () => {
    const wrapper = mountPage()
    await flushPromises()

    const evaluateButtons = wrapper.findAll('button').filter((item) => item.text().includes('评估'))
    await evaluateButtons[1].trigger('click')
    await flushPromises()

    expect(evaluateModel).toHaveBeenCalledWith(2, {
      split: 'val',
      conf: 0.001,
      iou: 0.6,
    })
    expect(getModelEvaluation).toHaveBeenCalledWith(2)
    expect(wrapper.text()).toContain('82.0%')
    expect(wrapper.text()).toContain('defect')
  })
})
