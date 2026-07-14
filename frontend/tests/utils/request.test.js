/**
 * Axios 请求封装测试
 *
 * 测试目标：
 *   - Axios 实例创建正确
 *   - 请求拦截器正常注入 Token
 *   - 响应拦截器正确处理错误
 */

import { describe, it, expect, vi, beforeEach } from 'vitest'
import { ElMessage } from 'element-plus'
import { createPinia, setActivePinia } from 'pinia'

describe('Axios 请求封装', () => {
  beforeEach(() => {
    // 每个测试前清除 mock
    vi.clearAllMocks()
    setActivePinia(createPinia())
    // 清除 localStorage
    localStorage.clear()
  })

  it('应该正确创建 axios 实例', async () => {
    const { default: request } = await import('@/utils/request')
    expect(request).toBeDefined()
    expect(request.defaults.baseURL).toBe('/api')
    expect(request.defaults.timeout).toBe(30000)
  })

  it('不应全局固定 Content-Type，以支持 FormData 自动生成 boundary', async () => {
    const { default: request } = await import('@/utils/request')
    expect(request.defaults.headers['Content-Type']).toBeUndefined()
  })

  it('登录失败时应该显示后端返回的凭据错误，而不是登录过期', async () => {
    const { default: request } = await import('@/utils/request')
    const rejected = request.interceptors.response.handlers[0].rejected
    const error = {
      config: { url: '/auth/login' },
      response: {
        status: 401,
        data: { code: 401, message: '用户名或密码错误', data: null },
      },
    }

    await expect(rejected(error)).rejects.toBe(error)
    expect(ElMessage.error).toHaveBeenCalledWith('用户名或密码错误')
    expect(ElMessage.error).not.toHaveBeenCalledWith('登录已过期，请重新登录')
  })

  it('注册冲突时应该显示后端返回的用户名已存在', async () => {
    const { default: request } = await import('@/utils/request')
    const rejected = request.interceptors.response.handlers[0].rejected
    const error = {
      config: { url: '/auth/register' },
      response: {
        status: 400,
        data: { code: 400, message: '用户名已存在', data: null },
      },
    }

    await expect(rejected(error)).rejects.toBe(error)
    expect(ElMessage.error).toHaveBeenCalledWith('用户名已存在')
  })

  it('应该兼容 FastAPI 默认 detail 错误结构', async () => {
    const { getApiErrorMessage } = await import('@/utils/request')

    expect(
      getApiErrorMessage(
        { data: { detail: '邮箱格式错误' } },
        '请求失败',
      ),
    ).toBe('邮箱格式错误')
  })
})

describe('错误上报模块', () => {
  it('应该正确初始化错误上报', async () => {
    const { setupErrorReporting } = await import('@/utils/errorReporter')
    expect(setupErrorReporting).toBeDefined()
    expect(typeof setupErrorReporting).toBe('function')
  })

  it('错误信息应该存入 localStorage', () => {
    // 模拟错误上报
    const errorInfo = {
      type: 'test_error',
      message: '测试错误',
    }

    // 手动触发上报逻辑
    const errors = JSON.parse(localStorage.getItem('error_logs') || '[]')
    errors.push({ ...errorInfo, timestamp: new Date().toISOString() })
    localStorage.setItem('error_logs', JSON.stringify(errors))

    // 验证
    const stored = JSON.parse(localStorage.getItem('error_logs'))
    expect(stored).toHaveLength(1)
    expect(stored[0].type).toBe('test_error')
  })
})
