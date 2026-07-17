import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import ElementPlus, { ElMessageBox } from 'element-plus'

const getModelVersions = vi.fn()
const getCurrentModel = vi.fn()
const activateModel = vi.fn()
const evaluateModel = vi.fn()
const getModelEvaluation = vi.fn()
const archiveModel = vi.fn()
const unarchiveModel = vi.fn()
const backupModel = vi.fn()
const restoreModel = vi.fn()
const deleteModel = vi.fn()

vi.mock('@/api/models', () => ({
  getModelVersions: (...args) => getModelVersions(...args),
  getCurrentModel: (...args) => getCurrentModel(...args),
  activateModel: (...args) => activateModel(...args),
  archiveModel: (...args) => archiveModel(...args),
  unarchiveModel: (...args) => unarchiveModel(...args),
  backupModel: (...args) => backupModel(...args),
  restoreModel: (...args) => restoreModel(...args),
  deleteModel: (...args) => deleteModel(...args),
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
  backup_available: true,
  backed_up_at: '2026-07-17T11:00:00',
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
    archiveModel.mockResolvedValue({ message: '归档成功' })
    unarchiveModel.mockResolvedValue({ message: '恢复成功' })
    backupModel.mockResolvedValue({ message: '备份成功' })
    restoreModel.mockResolvedValue({ message: '恢复成功' })
    deleteModel.mockResolvedValue({ message: '删除成功' })
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

  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('展示当前模型与全部版本，并可切换全局模型', async () => {
    vi.spyOn(ElMessageBox, 'confirm').mockResolvedValue('confirm')
    const wrapper = mountPage()
    await flushPromises()

    expect(wrapper.text()).toContain('当前全局检测模型')
    expect(wrapper.text()).toContain('v2.0.0')
    expect(wrapper.text()).toContain('v3.0.0')
    expect(wrapper.text()).toContain('已备份')

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

  it('取消删除确认时不报错、不调用删除接口，且提示不暴露内部路径', async () => {
    let confirmationText = ''
    vi.spyOn(ElMessageBox, 'confirm').mockImplementation((message) => {
      confirmationText = message
      return Promise.reject('cancel')
    })
    const wrapper = mountPage()
    await flushPromises()

    await wrapper.vm.handleMoreCommand('delete', candidate)
    await flushPromises()

    expect(deleteModel).not.toHaveBeenCalled()
    expect(confirmationText).not.toContain('backend/')
    expect(confirmationText).not.toContain('runs/train')
    expect(confirmationText).not.toContain('MinIO')
    expect(confirmationText).toContain('历史检测记录不受影响')
  })
})
