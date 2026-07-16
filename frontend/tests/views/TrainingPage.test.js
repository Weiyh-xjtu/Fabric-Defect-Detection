/**
 * TrainingPage 异步评估流程测试
 *
 * 重点验证：点击"模型评估"后立即返回并进入轮询，轮询到 completed 时渲染报告；
 * 选中任务时恢复进行中/已完成的评估状态；启动失败时提示错误且不进入轮询。
 * request 模块整体 mock，评估状态接口用队列模拟多次轮询的不同返回。
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { mount } from '@vue/test-utils'
import ElementPlus, { ElMessage } from 'element-plus'

const getMock = vi.fn()
const postMock = vi.fn()

vi.mock('@/utils/request', () => ({
  default: {
    get: (...args) => getMock(...args),
    post: (...args) => postMock(...args),
  },
}))

vi.mock('echarts', () => ({
  init: vi.fn(() => ({ setOption: vi.fn(), dispose: vi.fn() })),
}))

import TrainingPage from '@/views/TrainingPage.vue'

const completedTask = {
  id: 1,
  task_uuid: 'abc12345',
  status: 'completed',
  model_name: 'yolo11n',
  device: 'cpu',
  epochs: 50,
  current_epoch: 50,
  progress: 100,
  created_at: '2026-07-17 10:00:00',
}

const sampleReport = {
  task_id: 1,
  task_uuid: 'abc12345',
  split: 'val',
  overall: { precision: 0.81, recall: 0.72, map50: 0.83, map50_95: 0.51 },
  per_class: { defect: { ap50: 0.83, ap50_95: 0.51, instances: 12 } },
  model_version_id: 3,
  model_version: 'v1.0.0',
}

// 评估状态接口的返回队列：每次轮询取出一个，最后一个保留复用
let evalStatusResponses = []

function mountPage() {
  return mount(TrainingPage, { global: { plugins: [ElementPlus] } })
}

/** 推进假定时器并多轮清空微任务队列，确保请求回调与渲染完成 */
async function flushAll(ms = 0) {
  await vi.advanceTimersByTimeAsync(ms)
  await vi.advanceTimersByTimeAsync(0)
  await vi.advanceTimersByTimeAsync(0)
}

function findButton(wrapper, text) {
  return wrapper.findAll('button').find((b) => b.text().includes(text))
}

async function selectFirstTask(wrapper) {
  await findButton(wrapper, '监控').trigger('click')
  await flushAll()
}

describe('TrainingPage 异步模型评估', () => {
  beforeEach(() => {
    vi.useFakeTimers()
    vi.clearAllMocks()

    getMock.mockImplementation((url) => {
      if (url === '/training/scenes') return Promise.resolve({ items: [] })
      if (url === '/training/tasks') {
        return Promise.resolve({ items: [{ ...completedTask }] })
      }
      if (url.startsWith('/training/metrics/')) {
        return Promise.resolve({ metrics: [] })
      }
      if (url.startsWith('/training/status/')) {
        return Promise.resolve({
          task: { ...completedTask },
          latest_metric: null,
          is_running: false,
        })
      }
      if (url === '/training/validate/1/status') {
        const next =
          evalStatusResponses.length > 1
            ? evalStatusResponses.shift()
            : evalStatusResponses[0]
        return Promise.resolve(next)
      }
      return Promise.resolve({})
    })
    postMock.mockResolvedValue({
      task_id: 1,
      status: 'running',
      split: 'val',
      message: '评估任务已启动',
    })
  })

  afterEach(() => {
    vi.useRealTimers()
  })

  it('点击模型评估后启动任务并轮询到完成，渲染评估报告', async () => {
    evalStatusResponses = [
      { task_id: 1, status: 'idle' },
      { task_id: 1, status: 'running', split: 'val' },
      { task_id: 1, status: 'completed', split: 'val', report: sampleReport },
    ]

    const wrapper = mountPage()
    await flushAll()
    await selectFirstTask(wrapper)

    await findButton(wrapper, '模型评估').trigger('click')
    await flushAll()

    expect(postMock).toHaveBeenCalledWith('/training/validate/1', {
      split: 'val',
      conf: 0.001,
      iou: 0.6,
    })
    // 启动后立刻只是提示后台执行，还没有报告（分类 AP 表格属于评估报告区域）
    expect(wrapper.text()).not.toContain('分类 AP')

    // 第一次轮询：running，继续等待
    await flushAll(3000)
    expect(wrapper.text()).not.toContain('分类 AP')

    // 第二次轮询：completed，渲染报告
    await flushAll(3000)
    expect(ElMessage.success).toHaveBeenCalledWith('模型评估完成')
    expect(wrapper.text()).toContain('分类 AP')
    expect(wrapper.text()).toContain('83.0%')
    expect(wrapper.text()).toContain('defect')
  })

  it('选中任务时评估仍在进行则恢复轮询直至完成', async () => {
    evalStatusResponses = [
      { task_id: 1, status: 'running', split: 'val' },
      { task_id: 1, status: 'completed', split: 'val', report: sampleReport },
    ]

    const wrapper = mountPage()
    await flushAll()
    await selectFirstTask(wrapper)

    // 未手动点击评估，仅通过状态恢复进入轮询
    expect(postMock).not.toHaveBeenCalled()
    await flushAll(3000)
    expect(wrapper.text()).toContain('83.0%')
  })

  it('选中任务时已有完成的评估则直接展示报告', async () => {
    evalStatusResponses = [
      { task_id: 1, status: 'completed', split: 'val', report: sampleReport },
    ]

    const wrapper = mountPage()
    await flushAll()
    await selectFirstTask(wrapper)

    expect(wrapper.text()).toContain('83.0%')
    expect(wrapper.text()).toContain('defect')
    // 静默恢复历史报告，不重复弹"评估完成"
    expect(ElMessage.success).not.toHaveBeenCalled()
  })

  it('启动评估失败时提示错误且不进入轮询', async () => {
    evalStatusResponses = [{ task_id: 1, status: 'idle' }]
    postMock.mockRejectedValue({
      response: { data: { detail: '该任务已有评估正在进行，请等待完成' } },
    })

    const wrapper = mountPage()
    await flushAll()
    await selectFirstTask(wrapper)

    const statusCallsBefore = getMock.mock.calls.filter(
      ([url]) => url === '/training/validate/1/status',
    ).length

    await findButton(wrapper, '模型评估').trigger('click')
    await flushAll()
    expect(ElMessage.error).toHaveBeenCalledWith(
      '该任务已有评估正在进行，请等待完成',
    )

    // 失败后不应再有新的评估状态轮询
    await flushAll(10000)
    const statusCallsAfter = getMock.mock.calls.filter(
      ([url]) => url === '/training/validate/1/status',
    ).length
    expect(statusCallsAfter).toBe(statusCallsBefore)
  })
})
