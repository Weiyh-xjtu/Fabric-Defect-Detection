/**
 * 数据看板 API 参数组装测试
 *
 * 测试目标：
 *   - days 预设与自定义时间段互斥，start/end 优先
 *   - 缺陷类别数组作为 class_name 透传，且使用重复键序列化（indexes: null）
 *   - 缺陷下拉请求剔除已选缺陷，避免自我过滤
 */
import { beforeEach, describe, expect, it, vi } from 'vitest'

const get = vi.fn(() => Promise.resolve({}))

vi.mock('@/utils/request', () => ({
  default: { get: (...args) => get(...args) },
}))

let api

beforeEach(async () => {
  vi.clearAllMocks()
  api = await import('@/api/dashboard')
})

describe('数据看板 API 参数组装', () => {
  it('默认使用 days，不带时间段', async () => {
    await api.getStatistics({ days: 7 })
    const [url, config] = get.mock.calls[0]
    expect(url).toBe('/dashboard/statistics')
    expect(config.params).toEqual({ days: 7 })
    // 多值序列化关闭方括号索引，保证 class_name=a&class_name=b
    expect(config.paramsSerializer).toEqual({ indexes: null })
  })

  it('提供 start/end 时按时间段查询且忽略 days', async () => {
    await api.getTrend({ days: 30, start: '2026-06-01', end: '2026-06-30' })
    const [, config] = get.mock.calls[0]
    expect(config.params).toEqual({
      start_date: '2026-06-01',
      end_date: '2026-06-30',
    })
    expect(config.params.days).toBeUndefined()
  })

  it('缺陷类别数组透传为 class_name', async () => {
    await api.getClassDistribution({ days: 30, classNames: ['hole', 'stain'] })
    const [, config] = get.mock.calls[0]
    expect(config.params.class_name).toEqual(['hole', 'stain'])
  })

  it('空缺陷数组不带 class_name', async () => {
    await api.getStatistics({ days: 30, classNames: [] })
    const [, config] = get.mock.calls[0]
    expect(config.params.class_name).toBeUndefined()
  })

  it('缺陷趋势支持 topN 映射为 top_n', async () => {
    await api.getDefectTrend({ days: 30, topN: 5 })
    const [url, config] = get.mock.calls[0]
    expect(url).toBe('/dashboard/defect-trend')
    expect(config.params.top_n).toBe(5)
  })

  it('缺陷下拉请求剔除已选缺陷，避免自我过滤', async () => {
    await api.getDefectOptions({ days: 30, classNames: ['hole'] })
    const [url, config] = get.mock.calls[0]
    expect(url).toBe('/dashboard/defect-options')
    expect(config.params.class_name).toBeUndefined()
    expect(config.params.days).toBe(30)
  })
})
